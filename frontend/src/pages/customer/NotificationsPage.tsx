import React, { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Card, List, Avatar, Tag, Button, Typography, Tabs, Space, Badge, message } from 'antd';
import { BellOutlined, TagsOutlined, TeamOutlined, InboxOutlined, CheckOutlined } from '@ant-design/icons';
import { useAuth } from '../../context/AuthContext';
import { fetchNotifications, type NotificationItem } from '../../services/notificationService';

const { Title, Text, Paragraph } = Typography;

export const NotificationsPage: React.FC = () => {
  const navigate = useNavigate();
  const { user } = useAuth();
  const [items, setItems] = useState<NotificationItem[]>([]);
  const [filterType, setFilterType] = useState<string>('all');
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const load = async () => {
      if (!user) { setLoading(false); return; }
      try { setItems(await fetchNotifications(user.id)); } finally { setLoading(false); }
    };
    load();
  }, [user]);

  const filteredItems = items.filter((item) => filterType === 'all' || item.type === filterType);
  const offerCount = items.filter((item) => item.type === 'offer').length;

  const openNotification = (item: NotificationItem) => {
    if (item.action_url?.startsWith('/customer/')) navigate(item.action_url);
  };

  const markAllRead = async () => {
    if (!user) return;
    // local update only; backend update can be added later
    setItems(items.map((n) => ({ ...n, unread: false })));
    message.success('All notifications marked as read.');
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      <Card style={{ background: '#FFFFFF', borderColor: '#EBECF0' }} styles={{ body: { padding: '20px 24px' } }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <div>
            <Title level={4} style={{ margin: 0, color: '#172B4D' }}>Notifications & Demand Alerts</Title>
            <Paragraph type="secondary" style={{ margin: '4px 0 0 0', fontSize: 13 }}>Real-time updates regarding your demand groups, merchant proposals, and order milestones.</Paragraph>
          </div>
          <Button icon={<CheckOutlined />} onClick={markAllRead}>Mark All as Read</Button>
        </div>
      </Card>
      <Card style={{ background: '#FFFFFF', borderColor: '#EBECF0' }} styles={{ body: { padding: 16 } }}>
        <Tabs activeKey={filterType} onChange={(key) => setFilterType(key)} items={[{ key: 'all', label: `All Alerts (${items.length})` }, { key: 'offer', label: `Merchant Offers (${offerCount})` }, { key: 'demand', label: 'Demand Groups' }, { key: 'order', label: 'Order Tracking' }]} />
        {filteredItems.length === 0 ? <div style={{ padding: 40, textAlign: 'center', color: '#6B778C' }}>No data available yet</div> : (
          <List itemLayout="horizontal" dataSource={filteredItems} renderItem={(item) => (
            <List.Item
              onClick={() => openNotification(item)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' || event.key === ' ') openNotification(item);
              }}
              role={item.action_url ? 'link' : undefined}
              tabIndex={item.action_url ? 0 : undefined}
              style={{ padding: '14px 18px', background: item.unread ? '#F0F7FF' : 'transparent', borderRadius: 8, marginBottom: 8, border: item.unread ? '1px solid #BAE0FF' : '1px solid transparent', cursor: item.action_url ? 'pointer' : 'default' }}
            >
              <List.Item.Meta avatar={<Avatar style={{ backgroundColor: item.type === 'offer' ? '#1677FF' : item.type === 'demand' ? '#52C41A' : item.type === 'order' ? '#722ED1' : '#FAAD14' }} icon={item.type === 'offer' ? <TagsOutlined /> : item.type === 'demand' ? <TeamOutlined /> : <InboxOutlined />} />} title={<Space><span style={{ fontWeight: item.unread ? 700 : 500, fontSize: 14 }}>{item.title}</span>{item.unread && <Tag color="blue">New</Tag>}</Space>} description={<div><div style={{ color: '#172B4D', fontSize: 13, marginTop: 2 }}>{item.description}</div><div style={{ color: '#8993A4', fontSize: 11, marginTop: 4 }}>{item.created_at}</div></div>} />
            </List.Item>
          )} />
        )}
      </Card>
    </div>
  );
};

export default NotificationsPage;