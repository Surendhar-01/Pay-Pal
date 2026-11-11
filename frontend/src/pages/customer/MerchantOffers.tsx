import React, { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Card, Table, Tag, Button, Typography, Alert, Rate, Row, Col } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { DiffOutlined, SafetyCertificateOutlined, CarOutlined } from '@ant-design/icons';
import { customerTotal, fetchCustomerOffers, formatOfferCurrency, offerError, type CustomerOffer } from '../../services/offerService';

const { Title, Text, Paragraph } = Typography;

export const MerchantOffers: React.FC = () => {
  const navigate = useNavigate();
  const [offers, setOffers] = useState<CustomerOffer[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadOffers = async () => {
    setLoading(true);
    try { setOffers(await fetchCustomerOffers()); setError(null); }
    catch (failure) { setError(offerError(failure)); }
    finally { setLoading(false); }
  };
  useEffect(() => { loadOffers(); }, []);

  const columns: ColumnsType<CustomerOffer> = [
    { title: 'Offer Code', dataIndex: 'offer_code', key: 'offer_code', render: (code) => <Text strong style={{ color: '#1677FF' }}>{code}</Text> },
    { title: 'Merchant / Supplier', dataIndex: 'merchant_name', key: 'merchant_name', render: (name, record) => (<div><div style={{ fontWeight: 600 }}>{name ?? 'Unavailable'}</div>{record.merchant_rating != null && <Rate disabled value={Number(record.merchant_rating)} style={{ fontSize: 11, color: '#FAAD14' }} />}</div>) },
    { title: 'Demand Item', dataIndex: 'demand_title', key: 'demand_title', render: (title) => <span style={{ fontWeight: 500 }}>{title}</span> },
    { title: 'Unit Price', dataIndex: 'unit_price', render: (price, record) => formatOfferCurrency(price, record.currency) },
    { title: 'Your Quantity', dataIndex: 'customer_quantity' },
    { title: 'Your Total', render: (_, record) => formatOfferCurrency(customerTotal(record), record.currency) },
    { title: 'Delivery & Warranty', key: 'delivery', render: (_, record) => (<div><div><CarOutlined style={{ color: '#6B778C' }} /> {record.delivery_days} business days</div><div style={{ fontSize: 11, color: '#6B778C' }}>{record.warranty_months} months warranty</div></div>) },
    { title: 'Status', dataIndex: 'status', key: 'status', render: (status) => { let color = 'default'; if (status === 'Under Review') color = 'blue'; if (status === 'Customer Approved') color = 'green'; if (status === 'Rejected') color = 'red'; return <Tag color={color}>{status}</Tag>; } },
  ];

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      {error && <Alert type="error" title={error} action={<Button onClick={loadOffers}>Retry</Button>} />}
      <Card style={{ background: '#FFFFFF', borderColor: '#EBECF0' }} styles={{ body: { padding: '20px 24px' } }}>
        <Row justify="space-between" align="middle" gutter={[16, 16]}>
          <Col xs={24} md={16}><Title level={4} style={{ margin: 0, color: '#172B4D' }}>Competitive Merchant Offers</Title><Paragraph type="secondary" style={{ margin: '4px 0 0 0', fontSize: 13 }}>Compare eligible merchant proposals. Purchases are finalized only after explicit buyer approval.</Paragraph></Col>
          <Col xs={24} md={8} style={{ textAlign: 'right' }}><Button type="primary" icon={<DiffOutlined />} size="large" onClick={() => navigate('/customer/compare')}>Open Offer Comparison Matrix</Button></Col>
        </Row>
      </Card>
      <Alert title="Buyer Protection & Explicit Approval Gating" description="Merchants submit bids to your demand pools. You are under no obligation to buy until you explicitly review and approve a proposal." type="success" showIcon icon={<SafetyCertificateOutlined style={{ color: '#52C41A' }} />} style={{ background: '#F6FFED', borderColor: '#B7EB8F' }} />
      <Card style={{ background: '#FFFFFF', borderColor: '#EBECF0' }} styles={{ body: { padding: 0 } }}>
        <Table columns={columns} dataSource={offers} rowKey={offer => `${offer.customer_demand_id}:${offer.id}`} pagination={{ pageSize: 6 }} loading={loading} scroll={{ x: 1000 }} />
      </Card>
    </div>
  );
};

export default MerchantOffers;