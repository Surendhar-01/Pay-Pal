import { supabase } from './supabase';

export interface NotificationItem {
  id: string;
  user_id: string;
  title: string;
  description: string | null;
  type: 'demand' | 'offer' | 'order' | 'system';
  unread: boolean;
  action_url: string | null;
  created_at: string;
}

export const fetchNotifications = async (userId: string): Promise<NotificationItem[]> => {
  if (!supabase) return [];
  const { data, error } = await supabase
    .from('notifications')
    .select('*')
    .eq('user_id', userId)
    .order('created_at', { ascending: false });
  if (error) throw error;
  return data ?? [];
};