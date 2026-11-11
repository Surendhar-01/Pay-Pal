import { axiosClient } from './axiosClient';
import type { MatchedProduct, RequirementDraft } from './aiApi';

export const CHECKOUT_CONTEXT_KEY = 'markettwin_paypal_checkout_context_v1';
const CATALOGUE_CHECKOUT_TIMEOUT_MS = 90000;

export interface CheckoutContext {
  productId: string;
  requirements: RequirementDraft;
  idempotencyKey: string;
  matchClassification?: 'EXACT_MATCH' | 'CLOSEST_ALTERNATIVE';
}

export interface CatalogueQuote {
  product: MatchedProduct;
  product_id: string;
  quantity: number;
  unit_price: string;
  subtotal: string;
  shipping: string | null;
  tax: string | null;
  total: string;
  currency: string;
  paypal_conversion: PayPalConversion | null;
  paypal_conversion_error: string | null;
  match_classification: 'EXACT_MATCH' | 'CLOSEST_ALTERNATIVE';
}

export interface PayPalConversion {
  original_amount: string;
  original_currency: string;
  paypal_amount: string;
  paypal_currency: string;
  exchange_rate: string;
  exchange_rate_source: string;
  conversion_timestamp: string;
}

export interface PayPalOrderDetails extends PayPalConversion {
  paypal_order_id: string;
  approval_url: string;
  status: string;
}

export interface MarketTwinOrder {
  id: string;
  order_number: string;
  product_id: string;
  product_name: string;
  product_snapshot: MatchedProduct;
  quantity: number;
  unit_price: string;
  total_price: string;
  total_amount: string;
  currency: string;
  payment_status: string;
  order_status: string;
  match_classification: 'EXACT_MATCH' | 'CLOSEST_ALTERNATIVE' | null;
}

export const saveCheckoutContext = (context: CheckoutContext): void => {
  sessionStorage.setItem(CHECKOUT_CONTEXT_KEY, JSON.stringify(context));
};

export const readCheckoutContext = (): CheckoutContext | null => {
  try {
    const saved = sessionStorage.getItem(CHECKOUT_CONTEXT_KEY);
    if (!saved) return null;
    const parsed: unknown = JSON.parse(saved);
    if (
      !parsed ||
      typeof parsed !== 'object' ||
      typeof (parsed as CheckoutContext).productId !== 'string' ||
      typeof (parsed as CheckoutContext).idempotencyKey !== 'string' ||
      ((parsed as CheckoutContext).matchClassification !== undefined &&
        !['EXACT_MATCH', 'CLOSEST_ALTERNATIVE'].includes((parsed as CheckoutContext).matchClassification || '')) ||
      !(parsed as CheckoutContext).requirements
    ) {
      return null;
    }
    return parsed as CheckoutContext;
  } catch {
    return null;
  }
};

export const quoteCatalogueCheckout = async (
  context: CheckoutContext,
  quantity: number
): Promise<CatalogueQuote> => {
  const { data } = await axiosClient.post<CatalogueQuote>('/api/v1/customer/orders/quote', {
    product_id: context.productId,
    quantity,
    requirements: context.requirements,
    match_classification: context.matchClassification || 'EXACT_MATCH',
  }, { timeout: CATALOGUE_CHECKOUT_TIMEOUT_MS });
  return data;
};

export const createCatalogueCheckout = async (
  context: CheckoutContext,
  quantity: number
): Promise<MarketTwinOrder> => {
  const { data } = await axiosClient.post<MarketTwinOrder>('/api/v1/customer/orders/checkout', {
    product_id: context.productId,
    quantity,
    requirements: context.requirements,
    idempotency_key: context.idempotencyKey,
    match_classification: context.matchClassification || 'EXACT_MATCH',
  }, { timeout: CATALOGUE_CHECKOUT_TIMEOUT_MS });
  return data;
};

export const createPayPalOrder = async (
  orderId: string
): Promise<PayPalOrderDetails> => {
  const { data } = await axiosClient.post<PayPalOrderDetails>(
    `/api/v1/customer/orders/${encodeURIComponent(orderId)}/payments/paypal/create`
  );
  return data;
};

export const capturePayPalPayment = async (
  orderId: string,
  paypalOrderId: string
): Promise<{ status: string; capture_id: string }> => {
  const { data } = await axiosClient.post(
    `/api/v1/customer/orders/${encodeURIComponent(orderId)}/payments/paypal/capture`,
    { paypal_order_id: paypalOrderId }
  );
  return data;
};

export const cancelPayPalPayment = async (orderId: string): Promise<{ status: string }> => {
  const { data } = await axiosClient.post(
    `/api/v1/customer/orders/${encodeURIComponent(orderId)}/payments/paypal/cancel`
  );
  return data;
};

export const getCustomerOrder = async (orderId: string): Promise<MarketTwinOrder> => {
  const { data } = await axiosClient.get<MarketTwinOrder>(
    `/api/v1/customer/orders/${encodeURIComponent(orderId)}`
  );
  return data;
};
