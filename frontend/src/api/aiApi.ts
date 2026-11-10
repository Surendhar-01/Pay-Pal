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
  const response = await axiosClient.post<AIConversationResponse>(
    '/api/v1/ai/conversation',
    payload,
    { timeout: 520000 }
  );
  return response.data;
};

export const translateRequirementText = async (
  text: string,
  language: 'ta-IN' | 'hi-IN'
): Promise<string> => {
  const response = await axiosClient.post<{ translated_text: string }>(
    '/api/v1/ai/translate-requirement',
    { text, language },
    { timeout: 150000 }
  );
  return response.data.translated_text;
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
