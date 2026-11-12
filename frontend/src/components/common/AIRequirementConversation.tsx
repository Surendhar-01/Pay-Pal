import React, { FormEvent, KeyboardEvent, useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Alert,
  Button,
  Card,
  Divider,
  Input,
  Select,
  Spin,
  Table,
  Tag,
  Typography,
} from 'antd';
import type { TextAreaRef } from 'antd/es/input/TextArea';
import {
  ArrowRightOutlined,
  AudioOutlined,
  DeleteOutlined,
  EditOutlined,
  RobotOutlined,
  SendOutlined,
  ShoppingCartOutlined,
  ThunderboltOutlined,
  UserOutlined,
} from '@ant-design/icons';
import VoiceRequirementInput from './VoiceRequirementInput';
import {
  AIConversationPayload,
  ConversationMessage,
  MatchedProduct,
  ProductMatchingResponse,
  RequirementDraft,
  matchProductsToRequirements,
  sendAIConversationMessage,
} from '../../api/aiApi';
import { saveCheckoutContext } from '../../api/paymentApi';
import { useAuth } from '../../context/AuthContext';
import { supabase } from '../../services/supabase';
import { authReturnState } from '../../utils/authReturnLocation';
import { saveReviewedDemandDraft } from '../../utils/reviewedDemandDraft';
import { createSecureUuid } from '../../utils/secureUuid';
import './AIRequirementConversation.css';

const { Paragraph, Text, Title } = Typography;
const { TextArea } = Input;
const SESSION_KEY = 'markettwin_ai_requirement_conversation_v1';
const PRICE_FIELDS = [
  ['min_target_unit_price', 'Minimum unit price'],
  ['max_target_unit_price', 'Maximum unit price'],
  ['target_unit_price', 'Target unit price'],
  ['total_budget', 'Total order budget'],
] as const;
const SUPPORTED_CURRENCIES = new Set([
  'INR', 'USD', 'EUR', 'GBP', 'CAD', 'AUD', 'JPY', 'CNY', 'CHF', 'SGD', 'NZD',
]);
const EXAMPLE_PROMPTS = [
  'SS 316 water storage tank',
  'IoT gateway for industrial monitoring',
  'LiFePO4 energy module, 48V',
  'Ultrasonic flow meter',
];

interface DisplayMessage extends ConversationMessage {
  id: string;
}

interface SavedConversation {
  messages: DisplayMessage[];
  requirementDraft: RequirementDraft | null;
  ready: boolean;
}

interface FailedTurn {
  message: string;
  history: ConversationMessage[];
  requirementDraft: RequirementDraft | null;
}

const emptyDraft = (): RequirementDraft => ({
  product_name: null,
  product_type: null,
  category: null,
  mandatory_requirements: {},
  optional_preferences: {},
  commercial_constraints: {},
  delivery_constraints: {},
});

const makeMessage = (role: ConversationMessage['role'], content: string): DisplayMessage => ({
  id: `${Date.now()}-${Math.random().toString(36).slice(2)}`,
  role,
  content,
});

const restoreConversation = (): SavedConversation => {
  const empty: SavedConversation = { messages: [], requirementDraft: null, ready: false };
  try {
    const saved = sessionStorage.getItem(SESSION_KEY);
    if (!saved) return empty;
    const parsed: unknown = JSON.parse(saved);
    if (!parsed || typeof parsed !== 'object') return empty;

    const candidate = parsed as Partial<SavedConversation>;
    const messages = Array.isArray(candidate.messages)
      ? candidate.messages.filter(
          (item): item is DisplayMessage =>
            Boolean(item) &&
            (item.role === 'user' || item.role === 'assistant') &&
            typeof item.content === 'string' &&
            typeof item.id === 'string'
        )
      : [];
    const requirementDraft =
      candidate.requirementDraft && typeof candidate.requirementDraft === 'object'
        ? (candidate.requirementDraft as RequirementDraft)
        : null;
    return {
      messages,
      requirementDraft,
      ready: candidate.ready === true,
    };
  } catch {
    return empty;
  }
};

const formatLabel = (value: string, rawValue?: string): string => {
  if (value === 'budget') {
    if (rawValue && /\b(?:to|through|minimum|at least|over|above)\b|[–—]/i.test(rawValue)) {
      return 'Budget';
    }
    return 'Budget';
  }
  if (value === 'min_target_unit_price' || value === 'minimum_target_unit_price') {
    return 'Minimum unit price';
  }
  if (value === 'max_target_unit_price' || value === 'maximum_target_unit_price') {
    return 'Maximum unit price';
  }
  if (value === 'target_unit_price') return 'Target unit price';
  if (value === 'total_budget') return 'Total order budget';
  return value.replace(/_/g, ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());
};

const normalizeMoney = (value: string): { whole: string; fraction: string } | null => {
  const match = value.trim().match(/^(\d+)(?:\.(\d+))?$/);
  if (!match) return null;
  return {
    whole: match[1].replace(/^0+(?=\d)/, ''),
    fraction: (match[2] || '').replace(/0+$/, ''),
  };
};

const isPositiveMoney = (value: string): boolean => {
  const normalized = normalizeMoney(value);
  return Boolean(
    normalized &&
    (normalized.whole !== '0' || normalized.fraction.length > 0)
  );
};

const compareMoney = (left: string, right: string): number | null => {
  const a = normalizeMoney(left);
  const b = normalizeMoney(right);
  if (!a || !b) return null;
  if (a.whole.length !== b.whole.length) {
    return a.whole.length < b.whole.length ? -1 : 1;
  }
  if (a.whole !== b.whole) return a.whole < b.whole ? -1 : 1;
  const fractionLength = Math.max(a.fraction.length, b.fraction.length);
  const aFraction = a.fraction.padEnd(fractionLength, '0');
  const bFraction = b.fraction.padEnd(fractionLength, '0');
  if (aFraction === bFraction) return 0;
  return aFraction < bFraction ? -1 : 1;
};

const formatPrice = (value: number | null, currency: string | null): string => {
  if (value === null) return 'Not specified';
  if (!currency) return value.toLocaleString();
  try {
    return new Intl.NumberFormat(undefined, {
      style: 'currency',
      currency,
      maximumFractionDigits: 2,
    }).format(value);
  } catch {
    return `${value.toLocaleString()} ${currency}`;
  }
};

const formatRating = (product: MatchedProduct): string => {
  const ratingLabel = product.rating === null
    ? 'Rating unavailable'
    : `${product.rating.toFixed(1)}/5`;
  const reviewLabel = product.reviews === null
    ? 'review count unavailable'
    : `${product.reviews.toLocaleString()} reviews`;
  return `${ratingLabel} · ${reviewLabel}`;
};

const formatSpecification = (value: unknown): string => {
  if (value === null || value === undefined || value === '') return 'Not specified';
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
    return String(value);
  }
  return JSON.stringify(value);
};

const ProductResultCard: React.FC<{
  product: MatchedProduct;
  exact: boolean;
  onPurchase?: () => void;
}> = ({ product, exact, onPurchase }) => {
  const [imageIndex, setImageIndex] = useState(0);
  const imageUrls = product.images
    .map((image) => image.trim())
    .filter((image) => /^https?:\/\/\S+$/i.test(image));
  const imageUrl = imageUrls[imageIndex];
  const matchedRequirements = product.mandatory_results.filter((item) => item.state === 'PASS');
  const unresolvedRequirements = product.mandatory_results.filter((item) => item.state !== 'PASS');

  return (
    <Card className="ai-product-card" bordered>
      <div className="ai-product-card-main">
        {imageUrl ? (
          <img
            className="ai-product-image"
            src={imageUrl}
            alt={product.name || 'Catalogue product'}
            onError={() => setImageIndex((current) => current + 1)}
          />
        ) : (
          <div className="ai-product-image ai-product-image--empty">No catalogue image</div>
        )}
        <div className="ai-product-content">
          <div className="ai-product-title-row">
            <div>
              <Title level={5}>{product.name || 'Unnamed catalogue product'}</Title>
              <Text type="secondary">
                {product.brand || 'Brand not specified'}
                {product.category ? ` · ${product.category}` : ''}
                {' · ID: '}{product.product_id}
              </Text>
            </div>
            <Tag color={exact ? 'green' : 'gold'}>{exact ? 'Exact match' : 'Alternative · verify details'}</Tag>
          </div>
          <div className="ai-product-price">
            <Text strong>Effective price: {formatPrice(product.effective_price, product.currency)}</Text>
            {product.price !== null && product.price !== product.effective_price && (
              <Text delete type="secondary">Regular: {formatPrice(product.price, product.currency)}</Text>
            )}
            {product.currency === null && product.effective_price !== null && (
              <Text type="secondary">Catalogue currency not specified</Text>
            )}
          </div>
          <div className="ai-product-facts">
            <span>Rating: {formatRating(product)}</span>
            <span>Stock: {product.stock === null ? 'Not specified' : `${product.stock} available`}</span>
            <span>Delivery: {product.delivery_days === null ? 'Not specified' : `${product.delivery_days} days`}</span>
            {product.estimated_total !== null && (
              <span>
                Estimated product total: {formatPrice(product.estimated_total, product.currency)}
                {product.estimated_total_note ? ` (${product.estimated_total_note})` : ''}
              </span>
            )}
          </div>
          {matchedRequirements.length > 0 && (
            <div className="ai-product-checks">
              <Text strong>Verified requirements</Text>
              {matchedRequirements.map((item) => (
                <div key={`${item.attribute}-${item.requirement}`}>
                  <Tag color="green">PASS</Tag>
                  <Text>{formatLabel(item.attribute)}: {item.requirement}</Text>
                </div>
              ))}
            </div>
          )}
          {unresolvedRequirements.length > 0 && (
            <div className="ai-product-checks">
              <Text strong>{exact ? 'Requirement checks' : 'Why this is an alternative'}</Text>
              {unresolvedRequirements.map((item) => (
                <div key={`${item.attribute}-${item.requirement}`}>
                  <Tag color={item.state === 'FAIL' ? 'red' : 'gold'}>{item.state}</Tag>
                  <Text>{formatLabel(item.attribute)}: {item.requirement} — {item.reason}</Text>
                </div>
              ))}
            </div>
          )}
          <Paragraph className="ai-product-description">
            {product.description || 'No catalogue description provided.'}
          </Paragraph>
          {onPurchase && (
            <Button type="primary" icon={<ShoppingCartOutlined />} onClick={onPurchase}>
              Purchase
            </Button>
          )}
        </div>
      </div>
    </Card>
  );
};

const ProductMatchingResults: React.FC<{
  result: ProductMatchingResponse;
  onEdit: () => void;
  onCreateDemand: () => void;
  creatingDemand: boolean;
  onPurchase: (product: MatchedProduct, matchClassification: 'EXACT_MATCH' | 'CLOSEST_ALTERNATIVE') => void;
}> = ({ result, onEdit, onCreateDemand, creatingDemand, onPurchase }) => {
  const comparisonProducts = [...result.products, ...result.near_matches].slice(0, 3);
  const specificationKeys = Array.from(
    new Set(comparisonProducts.flatMap((product) => Object.keys(product.specifications)))
  );
  const comparisonRows = [
    { label: 'Brand', getValue: (product: MatchedProduct) => product.brand || 'Not specified' },
    { label: 'Product ID', getValue: (product: MatchedProduct) => product.product_id },
    { label: 'Effective price', getValue: (product: MatchedProduct) => formatPrice(product.effective_price, product.currency) },
    { label: 'Regular price', getValue: (product: MatchedProduct) => formatPrice(product.price, product.currency) },
    { label: 'Discount price', getValue: (product: MatchedProduct) => formatPrice(product.discount_price, product.currency) },
    { label: 'Rating / reviews', getValue: (product: MatchedProduct) => formatRating(product) },
    { label: 'Available stock', getValue: (product: MatchedProduct) => product.stock === null ? 'Not specified' : String(product.stock) },
    { label: 'Delivery estimate', getValue: (product: MatchedProduct) => product.delivery_days === null ? 'Not specified' : `${product.delivery_days} days` },
    ...specificationKeys.map((key) => ({
      label: formatLabel(key),
      getValue: (product: MatchedProduct) => formatSpecification(product.specifications[key]),
    })),
    {
      label: 'Mandatory checks',
      getValue: (product: MatchedProduct) => product.mandatory_results
        .map((item) => `${formatLabel(item.attribute)}: ${item.state}`)
        .join('; ') || 'No mandatory checks',
    },
  ];

  return (
    <section className="ai-match-results" aria-live="polite">
      <div className="ai-match-results-heading">
        <div>
          <Title level={3}>Product catalogue results</Title>
          <Text type="secondary">
            Evaluated {result.catalogue_products_examined.toLocaleString()} valid products
            {result.catalogue_total_reported !== null
              ? ` · API reported ${result.catalogue_total_reported.toLocaleString()} total`
              : ''}
          </Text>
        </div>
        <Button onClick={onEdit}>Edit requirements</Button>
      </div>
      {result.explanation && <Alert className="ai-match-explanation" type="info" showIcon title={result.explanation} />}
      {result.exact_match_count === 0 && (
        <Alert
          className="ai-match-status"
          type="warning"
          showIcon
          title="No exact product matches found in our catalogue."
          description="Relevant alternatives, if available, are shown separately below."
          action={<Button loading={creatingDemand} onClick={onCreateDemand}>Create Demand</Button>}
        />
      )}
      {result.products.length > 0 && (
        <>
          <Title level={4}>Exact Matches ({result.exact_match_count})</Title>
          {result.products.map((product) => (
            <ProductResultCard
              key={product.product_id}
              product={product}
              exact
              onPurchase={
                product.mandatory_results.every((item) => item.state === 'PASS') &&
                product.effective_price !== null &&
                /^[A-Z]{3}$/.test(product.currency || '') &&
                (product.stock === null || product.stock > 0)
                  ? () => onPurchase(product, 'EXACT_MATCH')
                  : undefined
              }
            />
          ))}
        </>
      )}
      {result.near_matches.length > 0 && (
        <>
          <Title level={4}>Closest Alternatives ({result.near_match_count})</Title>
          {result.near_matches.map((product) => (
            <ProductResultCard
              key={product.product_id}
              product={product}
              exact={false}
              onPurchase={
                product.effective_price !== null &&
                /^[A-Z]{3}$/.test(product.currency || '') &&
                (product.stock === null || product.stock > 0) &&
                product.mandatory_results
                  .filter((item) => item.source === 'commercial_constraints' || item.source === 'delivery_constraints')
                  .every((item) => item.state === 'PASS')
                  ? () => onPurchase(product, 'CLOSEST_ALTERNATIVE')
                  : undefined
              }
            />
          ))}
        </>
      )}
      {result.excluded_products.length > 0 && (
        <details className="ai-excluded-products">
          <summary>Excluded products (showing {result.excluded_products.length})</summary>
          {result.excluded_products.map((product) => (
            <div key={product.product_id}>
              <Text strong>{product.name || product.product_id}</Text>
              <ul>
                {product.mandatory_results
                  .filter((item) => item.state === 'FAIL')
                  .map((item) => (
                    <li key={`${item.attribute}-${item.requirement}`}>
                      {formatLabel(item.attribute)}: {item.reason}
                    </li>
                  ))}
              </ul>
            </div>
          ))}
        </details>
      )}
      {comparisonProducts.length > 1 && (
        <div className="ai-product-comparison">
          <Title level={4}>Compare leading candidates</Title>
          <Table
            bordered
            size="small"
            pagination={false}
            rowKey="label"
            dataSource={comparisonRows}
            columns={[
              { title: 'Details', dataIndex: 'label', key: 'label', fixed: 'left' },
              ...comparisonProducts.map((product) => ({
                title: product.name || product.product_id,
                key: product.product_id,
                render: (_: unknown, row: (typeof comparisonRows)[number]) => row.getValue(product),
              })),
            ]}
            scroll={{ x: 'max-content' }}
          />
        </div>
      )}
      <Button className="ai-match-edit-button" onClick={onEdit}>Edit requirements and search again</Button>
    </section>
  );
};

export const AIRequirementConversation: React.FC = () => {
  const navigate = useNavigate();
  const { role } = useAuth();
  const [saved] = useState(restoreConversation);
  const [messages, setMessages] = useState<DisplayMessage[]>(saved.messages);
  const [requirementDraft, setRequirementDraft] = useState<RequirementDraft | null>(saved.requirementDraft);
  const [ready, setReady] = useState(saved.ready);
  const [input, setInput] = useState('');
  const [inputMethod, setInputMethod] = useState<'type' | 'voice'>('type');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [failedTurn, setFailedTurn] = useState<FailedTurn | null>(null);
  const [matching, setMatching] = useState(false);
  const [matchingError, setMatchingError] = useState<string | null>(null);
  const [createDemandError, setCreateDemandError] = useState<string | null>(null);
  const [creatingDemand, setCreatingDemand] = useState(false);
  const [matchingResult, setMatchingResult] = useState<ProductMatchingResponse | null>(null);
  const [editingPrices, setEditingPrices] = useState(false);
  const [priceEdits, setPriceEdits] = useState<Record<string, string>>({});
  const [priceEditError, setPriceEditError] = useState<string | null>(null);
  const transcriptRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<TextAreaRef | null>(null);
  const matchingLock = useRef(false);
  const hasConversation = messages.length > 0;
  const hasProductDraft = Boolean(
    requirementDraft?.product_name || requirementDraft?.product_type
  );

  useEffect(() => {
    try {
      sessionStorage.setItem(
        SESSION_KEY,
        JSON.stringify({ messages, requirementDraft, ready } satisfies SavedConversation)
      );
    } catch {
      // The active conversation remains usable if browser storage is unavailable.
    }
  }, [messages, requirementDraft, ready]);

  useEffect(() => {
    transcriptRef.current?.scrollTo({
      top: transcriptRef.current.scrollHeight,
      behavior: 'smooth',
    });
  }, [messages, loading]);

  const requestAI = async (
    turn: FailedTurn,
    showUserMessage: boolean
  ): Promise<void> => {
    setLoading(true);
    setError(null);
    setFailedTurn(null);
    if (showUserMessage) {
      setMessages((current) => [...current, makeMessage('user', turn.message)]);
    }

    try {
      const response = await sendAIConversationMessage({
        message: turn.message,
        conversation_history: turn.history.slice(-12),
        requirement_draft: turn.requirementDraft,
      });
      setRequirementDraft(response.requirement_draft);
      setReady(response.ready_for_confirmation);
      setEditingPrices(false);
      setPriceEditError(null);
      setMatchingResult(null);
      setMatchingError(null);
      setMessages((current) => [...current, makeMessage('assistant', response.assistant_message)]);
    } catch (err) {
      setError(
        err instanceof Error && err.message
          ? err.message
          : 'MarketTwin AI is temporarily unavailable. Please try again.'
      );
      setFailedTurn(turn);
    } finally {
      setLoading(false);
    }
  };

  const submitMessage = async (value: string): Promise<void> => {
    const message = value.trim();
    if (!message || loading) return;

    const history = messages.map(({ role, content }) => ({ role, content }));
    setInput('');
    await requestAI({ message, history, requirementDraft }, true);
    inputRef.current?.focus();
  };

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    void submitMessage(input);
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      void submitMessage(input);
    }
  };

  const startNewConversation = () => {
    if (matchingLock.current) return;
    setMessages([]);
    setRequirementDraft(null);
    setReady(false);
    setInput('');
    setError(null);
    setFailedTurn(null);
    setMatchingError(null);
    setMatchingResult(null);
    setEditingPrices(false);
    setPriceEditError(null);
    try {
      sessionStorage.removeItem(SESSION_KEY);
    } catch {
      // Clearing browser storage is best-effort; the in-memory conversation is reset.
    }
    inputRef.current?.focus();
  };

  const editRequirements = () => {
    if (matchingLock.current) return;
    setMatchingResult(null);
    setMatchingError(null);
    setReady(false);
    inputRef.current?.focus();
  };

  const confirmRequirementsAndFindProducts = async (): Promise<void> => {
    if (!ready || !requirementDraft || matchingLock.current) return;
    if (!(requirementDraft.product_name || requirementDraft.product_type)) {
      setMatchingError('Add the product you are looking for before searching the catalogue.');
      return;
    }
    const quantity = Number(requirementDraft.commercial_constraints.quantity);
    if (!Number.isFinite(quantity) || quantity <= 0 || !requirementDraft.commercial_constraints.unit?.trim()) {
      setMatchingError('Provide a valid purchase quantity and unit before searching the catalogue.');
      return;
    }

    matchingLock.current = true;
    setMatching(true);
    setMatchingError(null);
    setMatchingResult(null);
    try {
      const result = await matchProductsToRequirements(requirementDraft);
      setMatchingResult(result);
    } catch (requestError) {
      setMatchingError(
        requestError instanceof Error
          ? requestError.message
          : 'Unable to search the product catalogue. Please try again.'
      );
    } finally {
      matchingLock.current = false;
      setMatching(false);
    }
  };

  const startDemandCreation = async (): Promise<void> => {
    const originalRequirement = messages.find((message) => message.role === 'user')?.content;
    if (
      !ready ||
      !requirementDraft ||
      !originalRequirement ||
      !saveReviewedDemandDraft(requirementDraft, originalRequirement, ready)
    ) {
      setCreateDemandError('Unable to preserve the confirmed requirement in this browser session. Please review and confirm your requirement again.');
      return;
    }
    setCreateDemandError(null);
    const destination = {
      pathname: '/customer/demands/create',
      search: '?source=ai',
      hash: '',
    };

    if (!supabase) {
      setCreateDemandError('Authentication is unavailable because Supabase is not configured.');
      return;
    }

    setCreatingDemand(true);
    try {
      const { data: sessionData, error: sessionError } =
        await supabase.auth.getSession();
      if (sessionError) throw sessionError;
      if (!sessionData.session) {
        navigate('/login?role=customer', {
          state: authReturnState(destination),
        });
        return;
      }

      const { data: authData, error: authError } = await supabase.auth.getUser();
      if (authError) throw authError;
      const authenticatedUser = authData.user;
      if (!authenticatedUser) {
        navigate('/login?role=customer', {
          state: authReturnState(destination),
        });
        return;
      }

      const { data: profile, error: profileError } = await supabase
        .from('profiles')
        .select('role')
        .eq('id', authenticatedUser.id)
        .maybeSingle();
      if (profileError) throw profileError;
      const actualRole =
        profile?.role ?? authenticatedUser.user_metadata?.role ?? role;
      if (actualRole !== 'customer') {
        setCreateDemandError('Only authenticated customer accounts can create a demand.');
        return;
      }
      if (!authenticatedUser.email_confirmed_at) {
        navigate('/auth/verify-email', {
          state: authReturnState(destination),
        });
        return;
      }
      navigate('/customer/demands/create?source=ai');
    } catch (requestError) {
      setCreateDemandError(
        requestError instanceof Error
          ? `Unable to verify your authentication: ${requestError.message}`
          : 'Unable to verify your authentication. Please try again.'
      );
    } finally {
      setCreatingDemand(false);
    }
  };

  const visibleMandatoryRequirements = requirementDraft
    ? Object.fromEntries(
        Object.entries(requirementDraft.mandatory_requirements)
          .filter(([key]) => key !== 'brand' && key !== 'model')
      )
    : {};

  const beginPriceEdit = () => {
    if (!requirementDraft) return;
    setPriceEdits(
      Object.fromEntries(
        [...PRICE_FIELDS.map(([key]) => key), 'currency', 'price_type'].map((key) => [
          key,
          requirementDraft.commercial_constraints[key] || '',
        ])
      )
    );
    setPriceEditError(null);
    setEditingPrices(true);
  };

  const savePriceEdits = () => {
    if (!requirementDraft) return;
    const enteredPrices = PRICE_FIELDS
      .map(([key]) => [key, priceEdits[key]?.trim() || ''] as const)
      .filter(([, value]) => value);
    if (enteredPrices.some(([, value]) => !isPositiveMoney(value))) {
      setPriceEditError('Enter positive monetary amounts using digits and an optional decimal point.');
      return;
    }
    if (
      priceEdits.min_target_unit_price &&
      priceEdits.max_target_unit_price &&
      compareMoney(
        priceEdits.min_target_unit_price,
        priceEdits.max_target_unit_price
      ) === 1
    ) {
      setPriceEditError('Minimum unit price cannot exceed maximum unit price.');
      return;
    }
    const currency = priceEdits.currency?.trim().toUpperCase() || '';
    if (enteredPrices.length && !SUPPORTED_CURRENCIES.has(currency)) {
      setPriceEditError('Choose a supported currency code before saving prices.');
      return;
    }
    if (!enteredPrices.length) {
      setPriceEditError('Keep at least one target price, price boundary, or total budget.');
      return;
    }

    const commercial = { ...requirementDraft.commercial_constraints };
    for (const [key] of PRICE_FIELDS) {
      const value = priceEdits[key]?.trim();
      if (value) commercial[key] = value;
      else delete commercial[key];
    }
    if (commercial.min_target_unit_price) {
      commercial.minimum_target_unit_price = commercial.min_target_unit_price;
    } else {
      delete commercial.minimum_target_unit_price;
    }
    if (commercial.max_target_unit_price) {
      commercial.maximum_target_unit_price = commercial.max_target_unit_price;
    } else {
      delete commercial.maximum_target_unit_price;
    }
    const previousTotal = requirementDraft.commercial_constraints.total_budget;
    if (previousTotal !== commercial.total_budget) {
      delete commercial.derived_max_unit_budget;
      delete commercial.derived_unit_budget_source;
      delete commercial.derived_max_unit_budget_is_derived;
    }
    if (currency) commercial.currency = currency;
    else delete commercial.currency;
    if (commercial.min_target_unit_price && commercial.max_target_unit_price) {
      commercial.price_type = 'range';
    } else if (commercial.max_target_unit_price) {
      commercial.price_type = 'maximum';
    } else if (commercial.min_target_unit_price) {
      commercial.price_type = 'minimum';
    } else if (commercial.target_unit_price) {
      commercial.price_type = priceEdits.price_type === 'approximate' ? 'approximate' : 'exact';
    } else {
      commercial.price_type = 'total_budget';
    }
    commercial.is_derived = 'false';
    delete commercial.price_clarification;
    setRequirementDraft({ ...requirementDraft, commercial_constraints: commercial });
    setMatchingResult(null);
    setMatchingError(null);
    setPriceEditError(null);
    setEditingPrices(false);
  };

  const renderDraftSection = (title: string, values: Record<string, string>) => {
    const entries = Object.entries(values).filter(
      ([key]) =>
        key !== 'minimum_target_unit_price' &&
        key !== 'maximum_target_unit_price' &&
      key !== 'derived_max_unit_budget_is_derived' &&
      key !== 'currency'
    );
    if (entries.length === 0) return null;
    return (
      <div className="ai-requirement-section">
        <Text strong>{title}</Text>
        <dl>
          {entries.map(([key, value]) => (
            <div className="ai-requirement-row" key={key}>
              <dt>{formatLabel(key, value)}</dt>
              <dd>
                {key === 'budget' && values.price_type === 'ambiguous_budget'
                  ? `${value} ${values.currency || ''} (scope needs clarification)`
                  : ['budget', ...PRICE_FIELDS.map(([priceKey]) => priceKey)].includes(key) && values.currency
                  ? `${value} ${values.currency}`
                  : value}
              </dd>
            </div>
          ))}
        </dl>
      </div>
    );
  };

  return (
    <main className={`ai-discovery-page${hasConversation ? ' ai-discovery-page--conversation' : ''}`}>
      {!hasConversation ? (
        <section className="ai-discovery-intro">
          <div className="ai-discovery-mark">
            <RobotOutlined />
            <span>MARKETTWIN AI</span>
          </div>
          <Title className="ai-discovery-title">Find the Right Products with AI</Title>
          <Paragraph className="ai-discovery-subtitle">
            Describe what you need. Speak or type your requirements, and let MarketTwin AI find the best products for you.
          </Paragraph>

          <form className="ai-composer ai-composer--hero" onSubmit={handleSubmit}>
            <div className="ai-composer-heading">
              <span className="ai-composer-sparkle"><ThunderboltOutlined /></span>
              <div>
                <Text className="ai-composer-title">What product are you looking for?</Text>
                <Text className="ai-composer-hint">Share the details that matter to you</Text>
              </div>
            </div>
            <TextArea
              className="ai-requirement-textarea"
              ref={inputRef}
              aria-label="English product requirement to submit"
              autoSize={{ minRows: 6, maxRows: 9 }}
              maxLength={2000}
              showCount
              value={input}
              onChange={(event) => setInput(event.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={'Describe the product you need...\n\nExample: I need an SS 304 industrial water storage tank with 1000-litre capacity.'}
              disabled={loading}
            />
            <div className="ai-input-methods" role="tablist" aria-label="Requirement input method">
              <button
                className={`ai-input-method${inputMethod === 'type' ? ' is-active' : ''}`}
                type="button"
                role="tab"
                aria-selected={inputMethod === 'type'}
                onClick={() => {
                  setInputMethod('type');
                  inputRef.current?.focus();
                }}
              >
                <EditOutlined />
                <span>Type Requirement</span>
              </button>
              <button
                className={`ai-input-method${inputMethod === 'voice' ? ' is-active' : ''}`}
                type="button"
                role="tab"
                aria-selected={inputMethod === 'voice'}
                onClick={() => setInputMethod('voice')}
              >
                <AudioOutlined />
                <span>Voice Input</span>
              </button>
            </div>
            {inputMethod === 'voice' && (
              <div className="ai-voice-panel">
                <VoiceRequirementInput value={input} onChange={setInput} disabled={loading} />
              </div>
            )}
            <div className="ai-composer-footer ai-composer-footer--hero">
              <Text type="secondary">Your requirements are reviewed before we find products.</Text>
              <Button
                className="ai-find-products-button"
                type="primary"
                htmlType="submit"
                icon={<ArrowRightOutlined />}
                loading={loading}
                disabled={!input.trim()}
              >
                Find Products
              </Button>
            </div>
          </form>

          {error && (
            <Alert
              className="ai-conversation-error"
              type="error"
              showIcon
              title={error}
              action={
                failedTurn && (
                  <Button
                    danger
                    type="link"
                    onClick={() => void requestAI(failedTurn, false)}
                  >
                    Retry
                  </Button>
                )
              }
            />
          )}

          <div className="ai-example-prompts">
            <Text type="secondary">Try an example</Text>
            <div>
              {EXAMPLE_PROMPTS.map((prompt) => (
                <button
                  className="ai-example-prompt"
                  key={prompt}
                  type="button"
                  disabled={loading}
                  onClick={() => void submitMessage(prompt)}
                >
                  {prompt}
                </button>
              ))}
            </div>
          </div>
          <Text className="ai-powered-by" type="secondary">
            Powered by MarketTwin AI · Qwen3
          </Text>
        </section>
      ) : (
        <section className="ai-conversation-shell">
          <header className="ai-conversation-heading">
            <div>
              <div className="ai-discovery-mark">
                <RobotOutlined />
                <span>MARKETTWIN AI</span>
              </div>
              <Title level={2}>Let’s define what you need.</Title>
              <Text type="secondary">Share details in your own words. Optional preferences can stay optional.</Text>
            </div>
            <Button icon={<DeleteOutlined />} onClick={startNewConversation} disabled={loading || matching}>
              New request
            </Button>
          </header>

          <div className="ai-conversation-transcript" ref={transcriptRef} aria-live="polite">
            {messages.map((item) => (
              <div className={`ai-message ai-message--${item.role}`} key={item.id}>
                <div className="ai-message-avatar">
                  {item.role === 'assistant' ? <RobotOutlined /> : <UserOutlined />}
                </div>
                <div className="ai-message-content">
                  <Text className="ai-message-author">
                    {item.role === 'assistant' ? 'MarketTwin AI' : 'You'}
                  </Text>
                  <Paragraph>{item.content}</Paragraph>
                </div>
              </div>
            ))}
            {loading && (
              <div className="ai-message ai-message--assistant">
                <div className="ai-message-avatar"><RobotOutlined /></div>
                <div className="ai-message-content ai-loading-message">
                  <Spin size="small" />
                  <Text type="secondary">MarketTwin AI is understanding your requirements...</Text>
                </div>
              </div>
            )}
          </div>

          {error && (
            <Alert
              className="ai-conversation-error"
              type="error"
              showIcon
              title={error}
              action={
                failedTurn && (
                  <Button
                    danger
                    type="link"
                    onClick={() => void requestAI(failedTurn, false)}
                  >
                    Retry
                  </Button>
                )
              }
            />
          )}

          {!ready && (
            <form className="ai-composer ai-composer--conversation" onSubmit={handleSubmit}>
              <Text className="ai-voice-input-label">English reply to MarketTwin AI</Text>
              <TextArea
                ref={inputRef}
                aria-label="Reply to MarketTwin AI"
                autoSize={{ minRows: 2, maxRows: 5 }}
                maxLength={2000}
                value={input}
                onChange={(event) => setInput(event.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="Type your answer..."
                disabled={loading}
              />
              <div className="ai-composer-footer">
                <Text type="secondary">Shift + Enter for a new line</Text>
                <div className="ai-composer-actions">
                  <VoiceRequirementInput value={input} onChange={setInput} disabled={loading} />
                  <Button
                    type="primary"
                    htmlType="submit"
                    icon={<SendOutlined />}
                    loading={loading}
                    disabled={!input.trim()}
                  >
                    Send
                  </Button>
                </div>
              </div>
            </form>
          )}

          {ready && hasProductDraft && requirementDraft && (
            <Card className="ai-requirement-card" bordered>
              <div className="ai-requirement-heading">
                <div>
                  <Tag color="green">Requirement draft ready</Tag>
                  <Title level={4}>Your requirement summary</Title>
                </div>
              </div>
              {(requirementDraft.product_name || requirementDraft.product_type) && (
                <div className="ai-requirement-product">
                  <Text type="secondary">Product</Text>
                  <Text strong>{requirementDraft.product_name || requirementDraft.product_type}</Text>
                </div>
              )}
              {requirementDraft.category && (
                <div className="ai-requirement-product">
                  <Text type="secondary">Category</Text>
                  <Text strong>{requirementDraft.category}</Text>
                </div>
              )}
              {requirementDraft.mandatory_requirements.brand && (
                <div className="ai-requirement-product">
                  <Text type="secondary">Brand</Text>
                  <Text strong>{requirementDraft.mandatory_requirements.brand}</Text>
                </div>
              )}
              {requirementDraft.mandatory_requirements.model && (
                <div className="ai-requirement-product">
                  <Text type="secondary">Model</Text>
                  <Text strong>{requirementDraft.mandatory_requirements.model}</Text>
                </div>
              )}
              {renderDraftSection('Mandatory requirements', visibleMandatoryRequirements)}
              {renderDraftSection('Optional preferences', requirementDraft.optional_preferences)}
              {!editingPrices ? (
                <>
                  {renderDraftSection('Commercial constraints', requirementDraft.commercial_constraints)}
                  <Button onClick={beginPriceEdit}>Edit price details</Button>
                </>
              ) : (
                <Card size="small" title="Edit price details">
                  {PRICE_FIELDS.map(([key, label]) => (
                    <label className="ai-requirement-price-edit" key={key}>
                      <Text>{label}</Text>
                      <Input
                        inputMode="decimal"
                        value={priceEdits[key] || ''}
                        onChange={(event) =>
                          setPriceEdits((current) => ({ ...current, [key]: event.target.value }))
                        }
                        aria-label={label}
                      />
                    </label>
                  ))}
                  {priceEdits.target_unit_price && (
                    <label className="ai-requirement-price-edit">
                      <Text>Target meaning</Text>
                      <Select
                        value={priceEdits.price_type === 'approximate' ? 'approximate' : 'exact'}
                        options={[
                          { value: 'exact', label: 'Exact target' },
                          { value: 'approximate', label: 'Approximate target' },
                        ]}
                        onChange={(value: string) =>
                          setPriceEdits((current) => ({ ...current, price_type: value }))
                        }
                        aria-label="Target price meaning"
                      />
                    </label>
                  )}
                  <label className="ai-requirement-price-edit">
                    <Text>Currency</Text>
                    <Input
                      maxLength={3}
                      value={priceEdits.currency || ''}
                      onChange={(event) =>
                        setPriceEdits((current) => ({ ...current, currency: event.target.value }))
                      }
                      aria-label="Currency code"
                    />
                  </label>
                  {priceEditError && (
                    <Alert className="ai-conversation-error" type="error" showIcon title={priceEditError} />
                  )}
                  <div className="ai-requirement-actions">
                    <Button type="primary" onClick={savePriceEdits}>Save price details</Button>
                    <Button onClick={() => setEditingPrices(false)}>Cancel</Button>
                  </div>
                </Card>
              )}
              {renderDraftSection('Delivery constraints', requirementDraft.delivery_constraints)}
              <div className="ai-requirement-actions">
                <Button
                  type="primary"
                  loading={matching}
                  disabled={matching || editingPrices}
                  onClick={() => void confirmRequirementsAndFindProducts()}
                >
                  {matching ? 'Searching the product catalogue...' : 'Confirm Requirements & Find Products'}
                </Button>
                <Button disabled={matching} onClick={editRequirements}>Edit Requirements</Button>
              </div>
              {matchingError && (
                <Alert
                  className="ai-conversation-error"
                  type="error"
                  showIcon
                  title={matchingError}
                  action={
                    <Button type="link" onClick={() => void confirmRequirementsAndFindProducts()}>
                      Retry
                    </Button>
                  }
                />
              )}
              {matching && (
                <div className="ai-matching-loading" role="status" aria-live="polite">
                  <Spin />
                  <Text type="secondary">Searching the product catalogue...</Text>
                </div>
              )}
              <Divider />
              <details className="ai-requirement-json">
                <summary>View structured JSON</summary>
                <pre>{JSON.stringify(requirementDraft, null, 2)}</pre>
              </details>
              <Text className="ai-phase-note" type="secondary">
                Confirming searches the catalogue only. Your requirements are not saved as a demand.
              </Text>
            </Card>
          )}
          {matchingResult && (
            <>
              {createDemandError && (
                <Alert type="error" showIcon title={createDemandError} />
              )}
              <ProductMatchingResults
                result={matchingResult}
                onEdit={editRequirements}
                onCreateDemand={() => void startDemandCreation()}
                creatingDemand={creatingDemand}
                onPurchase={(product, matchClassification) => {
                  if (!requirementDraft) return;
                  try {
                    saveCheckoutContext({
                      productId: product.product_id,
                      requirements: requirementDraft,
                      idempotencyKey: createSecureUuid(),
                      matchClassification,
                    });
                    navigate(`/customer/checkout?product_id=${encodeURIComponent(product.product_id)}`);
                  } catch (requestError) {
                    setCreateDemandError(
                      requestError instanceof Error
                        ? requestError.message
                        : 'Unable to securely prepare checkout. Please try again in a supported browser.'
                    );
                  }
                }}
              />
            </>
          )}
          <Text className="ai-powered-by" type="secondary">
            Powered by MarketTwin AI · Qwen3
          </Text>
        </section>
      )}
    </main>
  );
};

export default AIRequirementConversation;
