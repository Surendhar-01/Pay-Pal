import { axiosClient } from './axiosClient';

export interface ConversationMessage {
  role: 'user' | 'assistant';
  content: string;
}

export interface RequirementDraft {
  product_name: string | null;
  product_type: string | null;
  category: string | null;
  mandatory_requirements: Record<string, string>;
  optional_preferences: Record<string, string>;
  commercial_constraints: Record<string, string>;
  delivery_constraints: Record<string, string>;
}

export interface AIConversationResponse {
  assistant_message: string;
  status: 'collecting_information' | 'ready';
  requirement_draft: RequirementDraft;
  missing_essential_information: string[];
  clarification_questions: string[];
  ready_for_confirmation: boolean;
}

export interface AIConversationPayload {
  message: string;
  conversation_history: ConversationMessage[];
  requirement_draft: RequirementDraft | null;
}

export interface ConstraintEvaluation {
  source: string;
  attribute: string;
  requirement: string;
  state: 'PASS' | 'FAIL' | 'UNKNOWN';
  reason: string;
}

export interface PreferenceEvaluation {
  attribute: string;
  preference: string;
  state: 'PASS' | 'FAIL' | 'UNKNOWN';
  reason: string;
}

export interface MatchedProduct {
  product_id: string;
  name: string | null;
  brand: string | null;
  category: string | null;
  description: string | null;
  price: number | null;
  discount_price: number | null;
  effective_price: number | null;
  currency: string | null;
  images: string[];
  specifications: Record<string, unknown>;
  stock: number | null;
  delivery_days: number | null;
  rating: number | null;
  reviews: number | null;
  mandatory_results: ConstraintEvaluation[];
  preference_results: PreferenceEvaluation[];
  match_score: number;
  rating_confidence: number | null;
  bayesian_rating: number | null;
  estimated_total: number | null;
  estimated_total_note: string | null;
  missing_attributes: string[];
  explanation: string;
}

export interface ProductMatchingResponse {
  status: 'matches_found' | 'no_exact_matches' | 'no_relevant_products';
  catalogue_products_examined: number;
  catalogue_products_returned: number;
  catalogue_total_reported: number | null;
  exact_match_count: number;
  near_match_count: number;
  best_product_id: string | null;
  products: MatchedProduct[];
  near_matches: MatchedProduct[];
  excluded_products: MatchedProduct[];
  unverified_products: MatchedProduct[];
  unresolved_requirements: string[];
  explanation: string | null;
}

export const sendAIConversationMessage = async (
  payload: AIConversationPayload
): Promise<AIConversationResponse> => {
  const requestId = globalThis.crypto?.randomUUID?.()
    ?? `ai-${Date.now()}-${Math.random().toString(36).slice(2)}`;
  const startedAt = performance.now();
  const inputCharacters = payload.message.length
    + payload.conversation_history.reduce((total, item) => total + item.content.length, 0)
    + JSON.stringify(payload.requirement_draft ?? {}).length;
  const timeoutMs = 520000;
  const diagnostics = {
    request_id: requestId,
    provider: 'server-configured',
    model: 'server-configured',
    input_characters: inputCharacters,
    history_messages: payload.conversation_history.length,
    timeout_ms: timeoutMs,
  };

  console.info('[MarketTwin AI] request started', diagnostics);
  try {
    const response = await axiosClient.post<AIConversationResponse>(
      '/api/v1/ai/conversation',
      payload,
      { timeout: timeoutMs, headers: { 'X-Request-ID': requestId } }
    );
    return response.data;
  } catch (error) {
    const originalError = (error as Error & {
      originalError?: { code?: string; response?: { status?: number } };
    }).originalError;
    console.warn('[MarketTwin AI] request failed', {
      ...diagnostics,
      elapsed_ms: Math.round(performance.now() - startedAt),
      status_code: originalError?.response?.status,
      error_code: originalError?.code,
    });
    throw error;
  }
};

export const matchProductsToRequirements = async (
  requirementDraft: RequirementDraft
): Promise<ProductMatchingResponse> => {
  const response = await axiosClient.post<ProductMatchingResponse>(
    '/api/products/match',
    requirementDraft,
    { timeout: 120000 }
  );
  return response.data;
};
