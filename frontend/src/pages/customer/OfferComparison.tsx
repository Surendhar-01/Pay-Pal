import React, { useEffect, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import {
  Row,
  Col,
  Typography,
  Space,
  Dropdown,
  Modal,
  Checkbox,
  message,
  Rate,
  Alert,
  Select,
} from 'antd';
import {
  EyeOutlined,
  SyncOutlined,
  QuestionCircleOutlined,
  DownOutlined,
  CheckCircleOutlined,
  ArrowUpOutlined,
  TrophyOutlined,
  DiffOutlined,
} from '@ant-design/icons';
import { acceptOffer, customerTotal, fetchCustomerOffers, formatOfferCurrency, isCurrencyAvailable, offerError, type CustomerOffer } from '../../services/offerService';

const { Title, Text } = Typography;

export const OfferComparison: React.FC = () => {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const linkedOfferId = searchParams.get('offer') || '';
  const [syncing, setSyncing] = useState(false);
  const [selectedOffer, setSelectedOffer] = useState<CustomerOffer | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [demandId, setDemandId] = useState(searchParams.get('demand') || '');
  const [offers, setOffers] = useState<CustomerOffer[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const inFlight = useRef(false);
  const handleSync = async () => {
    setSyncing(true);
    try { setOffers(await fetchCustomerOffers()); setError(null); }
    catch (failure) { setError(offerError(failure)); }
    finally { setSyncing(false); }
  };
  useEffect(() => { handleSync(); }, []);
  const selectedDemandId = demandId || offers[0]?.customer_demand_id;
  const comparisonOffers = offers.filter(offer => offer.customer_demand_id === selectedDemandId)
    .sort((left, right) => Number(left.unit_price) - Number(right.unit_price));
  const deliveryDays = comparisonOffers.flatMap(offer => offer.delivery_days == null ? [] : [offer.delivery_days]);

  const handleApproveFinal = async () => {
    if (!selectedOffer || !confirmed || !isCurrencyAvailable(selectedOffer.currency) || inFlight.current) return;
    inFlight.current = true;
    setSaving(true);
    try {
      const result = await acceptOffer(selectedOffer, confirmed);
      message.success(`Order ${result.order.order_number} created. Payment pending.`);
      setSelectedOffer(null);
      setConfirmed(false);
      navigate('/customer/orders');
    } catch (failure) { message.error(offerError(failure)); }
    finally { inFlight.current = false; setSaving(false); }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 24, paddingBottom: 24 }}>
      {error && <Alert type="error" title={error} showIcon />}
      {/* 1. Header (Overview + Sync / Help / Time Filter) */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 12 }}>
        <div>
          <Title level={3} style={{ margin: 0, fontWeight: 700, color: '#172B4D', letterSpacing: -0.5 }}>
            Offer Comparison Matrix
          </Title>
          <Text type="secondary" style={{ fontSize: 13 }}>
            Evaluate side-by-side merchant proposals and authorize explicit final order approval
          </Text>
        </div>

        <Space size={10} align="center">
          <button
            onClick={handleSync}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 6,
              background: '#FFFFFF',
              border: '1px solid #E2E8F0',
              borderRadius: 20,
              padding: '6px 14px',
              fontSize: 13,
              fontWeight: 500,
              color: '#334155',
              cursor: 'pointer',
              boxShadow: '0 1px 2px rgba(0,0,0,0.03)',
            }}
          >
            <span>Sync</span>
            <SyncOutlined spin={syncing} style={{ fontSize: 12, color: '#64748B' }} />
          </button>

          <Select aria-label="Demand to compare" value={selectedDemandId} onChange={setDemandId}
            style={{ width: 240, maxWidth: '100%' }} loading={syncing}
            options={[...new Map(offers.map(offer => [offer.customer_demand_id, {
              value: offer.customer_demand_id, label: `${offer.demand_title ?? 'Demand'} (${offer.customer_quantity} units)`,
            }])).values()]} />
        </Space>
      </div>

      {/* 2. Top 4 Metric KPI Cards */}
      <Row gutter={[16, 16]}>
        <Col xs={24} sm={12} lg={6}>
          <div
            style={{
              backgroundColor: '#FFFFFF',
              border: '1px solid #E2E8F0',
              borderRadius: 14,
              padding: '18px 20px',
              boxShadow: '0 1px 3px rgba(0,0,0,0.04)',
            }}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
              <span style={{ color: '#475569', fontSize: 13, fontWeight: 500 }}>Comparing Proposals</span>
              <EyeOutlined style={{ color: '#94A3B8', fontSize: 14, cursor: 'pointer' }} />
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <span style={{ fontSize: 32, fontWeight: 700, color: '#0F172A', lineHeight: 1 }}>{comparisonOffers.length}</span>
              <span
                style={{
                  backgroundColor: '#ECFDF5',
                  color: '#059669',
                  borderRadius: 16,
                  padding: '2px 8px',
                  fontSize: 12,
                  fontWeight: 600,
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: 4,
                }}
              >
                <span style={{ width: 6, height: 6, borderRadius: '50%', backgroundColor: '#059669' }} />
                • Active Bids
              </span>
            </div>
          </div>
        </Col>

        <Col xs={24} sm={12} lg={6}>
          <div
            style={{
              backgroundColor: '#FFFFFF',
              border: '1px solid #E2E8F0',
              borderRadius: 14,
              padding: '18px 20px',
              boxShadow: '0 1px 3px rgba(0,0,0,0.04)',
            }}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
              <span style={{ color: '#475569', fontSize: 13, fontWeight: 500 }}>Lowest Offered Price</span>
              <EyeOutlined style={{ color: '#94A3B8', fontSize: 14, cursor: 'pointer' }} />
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <span style={{ fontSize: 24, fontWeight: 700, color: '#0F172A', lineHeight: 1 }}>{comparisonOffers.length ? formatOfferCurrency(comparisonOffers[0].unit_price, comparisonOffers[0].currency) : 'Not available'}</span>
              <span
                style={{
                  backgroundColor: '#EFF6FF',
                  color: '#2563EB',
                  borderRadius: 16,
                  padding: '2px 8px',
                  fontSize: 12,
                  fontWeight: 600,
                }}
              >
                Per unit
              </span>
            </div>
          </div>
        </Col>

        <Col xs={24} sm={12} lg={6}>
          <div
            style={{
              backgroundColor: '#FFFFFF',
              border: '1px solid #E2E8F0',
              borderRadius: 14,
              padding: '18px 20px',
              boxShadow: '0 1px 3px rgba(0,0,0,0.04)',
            }}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
              <span style={{ color: '#475569', fontSize: 13, fontWeight: 500 }}>Fastest Delivery</span>
              <EyeOutlined style={{ color: '#94A3B8', fontSize: 14, cursor: 'pointer' }} />
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
              <span style={{ fontSize: 24, fontWeight: 700, color: '#0F172A', lineHeight: 1 }}>{deliveryDays.length ? `${Math.min(...deliveryDays)} Days` : 'Not specified'}</span>
              <span
                style={{
                  backgroundColor: '#ECFDF5',
                  color: '#059669',
                  borderRadius: 16,
                  padding: '2px 8px',
                  fontSize: 12,
                  fontWeight: 600,
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: 3,
                }}
              >
                Business days
              </span>
            </div>
          </div>
        </Col>

        <Col xs={24} sm={12} lg={6}>
          <div
            style={{
              backgroundColor: '#FFFFFF',
              border: '1px solid #E2E8F0',
              borderRadius: 14,
              padding: '18px 20px',
              boxShadow: '0 1px 3px rgba(0,0,0,0.04)',
            }}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
              <span style={{ color: '#475569', fontSize: 13, fontWeight: 500 }}>Your Quantity</span>
              <EyeOutlined style={{ color: '#94A3B8', fontSize: 14, cursor: 'pointer' }} />
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span style={{ fontSize: 32, fontWeight: 700, color: '#0F172A', lineHeight: 1 }}>{comparisonOffers[0]?.customer_quantity ?? 0}</span>
              <span
                style={{
                  backgroundColor: '#ECFDF5',
                  color: '#059669',
                  borderRadius: 16,
                  padding: '2px 8px',
                  fontSize: 12,
                  fontWeight: 600,
                }}
              >
                Units
              </span>
            </div>
          </div>
        </Col>
      </Row>

      {/* 3. Comparison Cards Grid */}
      <Row gutter={[20, 20]}>
        {comparisonOffers.map((offer, index) => {
          const isBestValue = index === 0;
          const isLinkedOffer = offer.id === linkedOfferId;
          return (
            <Col xs={24} md={8} key={offer.id}>
              <div
                style={{
                  border: isLinkedOffer ? '3px solid #0F766E' : isBestValue ? '2px solid #2563EB' : '1px solid #E2E8F0',
                  borderRadius: 18,
                  backgroundColor: '#FFFFFF',
                  padding: '24px 22px',
                  boxShadow: isBestValue ? '0 8px 24px rgba(37, 99, 235, 0.12)' : '0 1px 3px rgba(0,0,0,0.03)',
                  display: 'flex',
                  flexDirection: 'column',
                  justifyContent: 'space-between',
                  height: '100%',
                  position: 'relative',
                }}
              >
                {isBestValue && (
                  <div
                    style={{
                      position: 'absolute',
                      top: -12,
                      left: '50%',
                      transform: 'translateX(-50%)',
                      backgroundColor: '#2563EB',
                      color: '#FFFFFF',
                      borderRadius: 14,
                      padding: '3px 14px',
                      fontSize: 11,
                      fontWeight: 700,
                      display: 'flex',
                      alignItems: 'center',
                      gap: 4,
                      boxShadow: '0 2px 8px rgba(37, 99, 235, 0.3)',
                    }}
                  >
                    <TrophyOutlined /> LOWEST UNIT PRICE
                  </div>
                )}

                <div>
                  <div style={{ textAlign: 'center', marginTop: isBestValue ? 8 : 0, marginBottom: 16 }}>
                    {isLinkedOffer && <div style={{ color: '#0F766E', fontSize: 11, fontWeight: 700, marginBottom: 6 }}>OFFER FROM YOUR NOTIFICATION</div>}
                    <div style={{ fontSize: 12, color: '#64748B', fontWeight: 500 }}>{offer.demand_title}</div>
                    <div style={{ fontSize: 17, fontWeight: 700, color: '#0F172A', margin: '4px 0' }}>
                      {offer.merchant_name ?? 'Unavailable'}
                    </div>
                    <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', gap: 6 }}>
                      {offer.merchant_rating != null && <Rate disabled value={Number(offer.merchant_rating)} style={{ fontSize: 12, color: '#F59E0B' }} />}
                      <span style={{ fontSize: 12, color: '#64748B', fontWeight: 600 }}>{offer.merchant_rating ?? 'Not rated'}</span>
                    </div>
                  </div>

                  {/* Price Banner */}
                  <div
                    style={{
                      background: isBestValue ? 'linear-gradient(135deg, #EFF6FF 0%, #EEF2FF 100%)' : '#F8FAFC',
                      border: isBestValue ? '1px solid #DBEAFE' : '1px solid #E2E8F0',
                      padding: '16px 14px',
                      borderRadius: 12,
                      textAlign: 'center',
                      marginBottom: 20,
                    }}
                  >
                    <div style={{ fontSize: 11, color: '#64748B', fontWeight: 600 }}>Offered Wholesale Unit Price</div>
                    <div style={{ fontSize: 32, fontWeight: 800, color: '#059669', margin: '2px 0' }}>
                      {formatOfferCurrency(offer.unit_price, offer.currency)}
                    </div>
                    <span
                      style={{
                        backgroundColor: '#ECFDF5',
                        color: '#059669',
                        borderRadius: 12,
                        padding: '2px 10px',
                        fontSize: 11.5,
                        fontWeight: 700,
                        display: 'inline-block',
                      }}
                    >
                      Your total: {formatOfferCurrency(customerTotal(offer), offer.currency)}
                    </span>
                  </div>

                  {/* Feature Breakdown Rows */}
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 12, fontSize: 13, marginBottom: 24 }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', paddingBottom: 8, borderBottom: '1px solid #F1F5F9' }}>
                      <span style={{ color: '#64748B' }}>Delivery Lead Time:</span>
                      <strong style={{ color: '#0F172A' }}>{offer.delivery_days ?? 'Not specified'} business days</strong>
                    </div>
                    <div style={{ display: 'flex', justifyContent: 'space-between', paddingBottom: 8, borderBottom: '1px solid #F1F5F9' }}>
                      <span style={{ color: '#64748B' }}>Manufacturer Warranty:</span>
                      <strong style={{ color: '#0F172A' }}>{offer.warranty_months ?? 'Not specified'} months</strong>
                    </div>
                    <div style={{ display: 'flex', justifyContent: 'space-between', paddingBottom: 8, borderBottom: '1px solid #F1F5F9' }}>
                      <span style={{ color: '#64748B' }}>Min Order Volume:</span>
                      <strong style={{ color: '#0F172A' }}>{offer.min_order_quantity ?? 'Not specified'} units</strong>
                    </div>
                    <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                      <span style={{ color: '#64748B' }}>Shipping Freight:</span>
                      <strong style={{ color: '#0F172A', overflowWrap: 'anywhere' }}>{offer.shipping_terms ?? 'Not specified'}</strong>
                    </div>
                  </div>
                </div>

                <button
                  disabled={offer.eligibility.status !== 'Eligible' || !isCurrencyAvailable(offer.currency) || saving}
                  title={!isCurrencyAvailable(offer.currency) ? 'Currency unavailable; this offer cannot be accepted.' : offer.eligibility.details.checks.join(' ')}
                  onClick={() => {
                    setSelectedOffer(offer);
                    setConfirmed(false);
                  }}
                  style={{
                    width: '100%',
                    backgroundColor: isBestValue ? '#2563EB' : '#0A101D',
                    color: '#FFFFFF',
                    border: 'none',
                    borderRadius: 20,
                    padding: '9px 0',
                    fontSize: 13,
                    fontWeight: 600,
                    cursor: 'pointer',
                    boxShadow: isBestValue ? '0 4px 12px rgba(37, 99, 235, 0.25)' : 'none',
                    transition: 'all 0.2s',
                  }}
                >
                  Approve This Offer
                </button>
              </div>
            </Col>
          );
        })}
      </Row>

      {/* Explicit Approval Modal */}
      <Modal
        title={
          <Space>
            <CheckCircleOutlined style={{ color: '#059669' }} />
            <span>Explicit Customer Approval</span>
          </Space>
        }
        open={Boolean(selectedOffer)}
        onCancel={() => { if (!saving) setSelectedOffer(null); }}
        footer={[
          <button
            key="cancel"
            disabled={saving}
            onClick={() => setSelectedOffer(null)}
            style={{
              backgroundColor: '#FFFFFF',
              border: '1px solid #CBD5E1',
              borderRadius: 18,
              padding: '6px 16px',
              fontSize: 12.5,
              fontWeight: 600,
              cursor: 'pointer',
              marginRight: 8,
            }}
          >
            Cancel
          </button>,
          <button
            key="approve"
            disabled={!confirmed || saving}
            onClick={handleApproveFinal}
            style={{
              backgroundColor: confirmed ? '#059669' : '#CBD5E1',
              color: '#FFFFFF',
              border: 'none',
              borderRadius: 18,
              padding: '6px 20px',
              fontSize: 12.5,
              fontWeight: 600,
              cursor: confirmed ? 'pointer' : 'not-allowed',
            }}
          >
            {saving ? 'Placing Order...' : 'Confirm & Place Order'}
          </button>,
        ]}
        width={580}
      >
        {selectedOffer && (
          <div style={{ padding: '12px 0' }}>
            <div style={{ background: '#F8FAFC', padding: 14, borderRadius: 10, border: '1px solid #E2E8F0', marginBottom: 14 }}>
              <div style={{ fontSize: 12, color: '#64748B' }}>Winning Supplier Proposal</div>
              <div style={{ fontSize: 16, fontWeight: 700, color: '#0F172A' }}>
                {selectedOffer.merchant_name}: {formatOfferCurrency(selectedOffer.unit_price, selectedOffer.currency)} per unit
              </div>
              <div style={{ fontSize: 12, color: '#059669', fontWeight: 600, marginTop: 4 }}>
                Demand: {selectedOffer.demand_title}; Delivery: {selectedOffer.delivery_days ?? 'Not specified'} Days
              </div>
            </div>

            <p>Quantity: {selectedOffer.customer_quantity}; Total: {formatOfferCurrency(customerTotal(selectedOffer), selectedOffer.currency)}</p>
            <p>Shipping: {selectedOffer.shipping_terms ?? 'Not specified'}</p>
            <p>Specifications: {selectedOffer.spec_notes ?? 'Not specified'}</p>
            <p>Warranty: {selectedOffer.warranty_months ?? 'Not specified'} months</p>
            <p style={{ fontSize: 13, color: '#475569', lineHeight: 1.5 }}>
              MarketTwin's core governance requires explicit customer approval before any contract binding or funds transfer occurs.
            </p>

            <div style={{ marginTop: 16, padding: '12px 14px', background: '#F0FDF4', border: '1px solid #BBF7D0', borderRadius: 8 }}>
              <Checkbox
                checked={confirmed}
                onChange={(e) => setConfirmed(e.target.checked)}
              >
                <span style={{ fontSize: 13, fontWeight: 600, color: '#065F46' }}>
                  I explicitly approve this offer ({selectedOffer.offer_code}) for {selectedOffer.customer_quantity} units and agree to the displayed terms.
                </span>
              </Checkbox>
            </div>
          </div>
        )}
      </Modal>
    </div>
  );
};

export default OfferComparison;
