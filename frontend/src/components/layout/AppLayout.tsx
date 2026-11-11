import React, { useState, useEffect, useRef } from 'react';
import { Outlet, useNavigate, useLocation, Link } from 'react-router-dom';
import {
  Layout,
  Menu,
  Button,
  Badge,
  Avatar,
  Dropdown,
  Space,
  Tag,
  Typography,
  Tooltip,
  Popover,
  List,
  Segmented,
  Input,
} from 'antd';
import type { MenuProps } from 'antd';
import {
  DashboardOutlined,
  ShoppingOutlined,
  SearchOutlined,
  FileAddOutlined,
  AppstoreOutlined,
  TeamOutlined,
  TagsOutlined,
  DiffOutlined,
  InboxOutlined,
  BellOutlined,
  UserOutlined,
  ShopOutlined,
  SafetyCertificateOutlined,
  LineChartOutlined,
  SettingOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  SyncOutlined,
  DatabaseOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  CarOutlined,
  SwapOutlined,
  DownOutlined,
  DollarCircleOutlined,
  LogoutOutlined,
  CustomerServiceOutlined,
} from '@ant-design/icons';
import healthApi from '../../api/healthApi';
import { HealthResponse } from '../../types';
import { profileInitials, useAuth } from '../../context/AuthContext';
import { env, isSupabaseConfigured } from '../../config/env';
import RoleSelectionModal from '../common/RoleSelectionModal';
import type { AuthAction } from '../common/RoleSelectionModal';
import { fetchNotifications, type NotificationItem } from '../../services/notificationService';
// mock data removed; notifications are loaded from Supabase when available

const { Header, Sider, Content, Footer } = Layout;
const { Text } = Typography;

export type ActiveRole = 'customer' | 'merchant' | 'admin';

export const AppLayout: React.FC = () => {
  const navigate = useNavigate();
  const location = useLocation();

  const { user, profile, role: authRole, signOut } = useAuth();
  const currentPath = location.pathname;
  const isHomePage = currentPath === '/';
  const activeRole: ActiveRole | null = authRole;
  const profileName = profile?.fullName?.trim() || 'Name unavailable';
  const profileEmail = profile?.email || 'Email unavailable';

  const [collapsed, setCollapsed] = useState(false);
  const [isBackendHealthy, setIsBackendHealthy] = useState<boolean | null>(null);
  const [healthData, setHealthData] = useState<HealthResponse | null>(null);
  const [pinging, setPinging] = useState(false);
  const [notifications, setNotifications] = useState<NotificationItem[]>([]);
  const [authAction, setAuthAction] = useState<AuthAction | null>(null);
  const [roleSelectionOpen, setRoleSelectionOpen] = useState(false);
  const roleNavigationStarted = useRef(false);

  const openRoleSelection = (action: AuthAction) => {
    roleNavigationStarted.current = false;
    setAuthAction(action);
    setRoleSelectionOpen(true);
  };

  const closeRoleSelection = () => {
    setRoleSelectionOpen(false);
    setAuthAction(null);
  };

  const navigateForRole = (selectedRole: ActiveRole) => {
    if (roleNavigationStarted.current || !authAction) return;
    if (authAction === 'register' && selectedRole === 'admin') return;

    roleNavigationStarted.current = true;
    const destination = authAction === 'login'
      ? `/auth/login?role=${selectedRole}`
      : `/auth/register/${selectedRole}`;
    closeRoleSelection();
    navigate(destination);
  };

  const handleLogout = async () => {
    // Move off any protected route first; defer signOut to a macrotask so the
    // navigation commits before the session is cleared, otherwise the
    // protected-route guard redirects to /login instead of home.
    navigate('/', { replace: true });
    await new Promise<void>((resolve) => {
      setTimeout(resolve, 0);
    });
    await signOut();
  };

  // Backend Health Polling
  const checkHealth = async () => {
    setPinging(true);
    try {
      const res = await healthApi.checkHealth();
      setHealthData(res);
      setIsBackendHealthy(res.status === 'ok');
    } catch {
      setIsBackendHealthy(false);
      setHealthData(null);
    } finally {
      setPinging(false);
    }
  };

  useEffect(() => {
    checkHealth();
    const interval = setInterval(checkHealth, 30000);
    return () => clearInterval(interval);
  }, []);

  useEffect(() => {
    let active = true;
    if (!user || activeRole !== 'customer') {
      setNotifications([]);
      return () => { active = false; };
    }

    const userId = user.id;
    const loadNotifications = async () => {
      try {
        const items = await fetchNotifications(userId);
        if (active) setNotifications(items);
      } catch {
        if (active) setNotifications([]);
      }
    };
    void loadNotifications();
    const interval = window.setInterval(loadNotifications, 30000);
    return () => {
      active = false;
      window.clearInterval(interval);
    };
  }, [user?.id, activeRole]);

  // Customer Navigation Items
  const customerMenuItems: MenuProps['items'] = [
    {
      key: '/customer/dashboard',
      icon: <DashboardOutlined />,
      label: 'Home',
    },
    {
      key: 'catalogue-group',
      icon: <ShoppingOutlined />,
      label: 'Products',
      children: [
        { key: '/customer/catalogue', label: 'Product Catalogue' },
        { key: '/customer/search', label: 'Search Products', icon: <SearchOutlined /> },
      ],
    },
    {
      key: 'demands-group',
      icon: <AppstoreOutlined />,
      label: 'Demands',
      children: [
        { key: '/customer/demands', label: 'My Demands' },
        { key: '/customer/demands/create', label: 'Create Demand', icon: <FileAddOutlined /> },
        { key: '/customer/groups', label: 'Demand Groups', icon: <TeamOutlined /> },
      ],
    },
    {
      key: 'offers-group',
      icon: <TagsOutlined />,
      label: 'Offers',
      children: [
        { key: '/customer/offers', label: 'Merchant Offers' },
        { key: '/customer/compare', label: 'Offer Comparison', icon: <DiffOutlined /> },
      ],
    },
    {
      key: '/customer/orders',
      icon: <InboxOutlined />,
      label: 'Orders & Tracking',
    },
    {
      key: '/customer/notifications',
      icon: <BellOutlined />,
      label: 'Notifications',
    },
    {
      key: '/customer/profile',
      icon: <UserOutlined />,
      label: 'Customer Profile',
    },
  ];

  // Merchant Navigation Items
  const merchantMenuItems: MenuProps['items'] = [
    {
      key: '/merchant/dashboard',
      icon: <DashboardOutlined />,
      label: 'Merchant Dashboard',
    },
    {
      key: '/merchant/demands',
      icon: <TeamOutlined />,
      label: 'Available Demands',
    },
    {
      key: 'offers-group',
      icon: <TagsOutlined />,
      label: 'Offer Management',
      children: [
        { key: '/merchant/offers/submit', label: 'Submit Offers', icon: <FileAddOutlined /> },
        { key: '/merchant/offers', label: 'My Submitted Offers' },
      ],
    },
    {
      key: '/merchant/orders',
      icon: <InboxOutlined />,
      label: 'Fulfillment Orders',
    },
    {
      key: '/merchant/profile',
      icon: <ShopOutlined />,
      label: 'Merchant Profile',
    },
  ];

  // Admin Navigation Items (SaaS design aligned with reference)
  const adminMenuItems: MenuProps['items'] = [
    {
      key: '/admin/dashboard',
      icon: <DashboardOutlined />,
      label: 'Overview',
    },
    {
      key: '/admin/offers',
      icon: <TagsOutlined />,
      label: 'Campaigns & Offers',
    },
    {
      key: '/admin/products',
      icon: <ShoppingOutlined />,
      label: 'Product Catalog',
    },
    {
      key: 'targeting-group',
      icon: <TeamOutlined />,
      label: 'Targeting & Demands',
      children: [
        { key: '/admin/demands', label: 'Customer Demands' },
        { key: '/admin/groups', label: 'Demand Groups' },
      ],
    },
    {
      key: '/admin/reports',
      icon: <LineChartOutlined />,
      label: 'Performance',
    },
    {
      key: 'payouts-group',
      icon: <DollarCircleOutlined />,
      label: 'Payouts & Escrow',
      children: [
        { key: '/admin/orders', label: 'Platform Orders' },
        { key: '/admin/merchants', label: 'Merchant Accounts' },
        { key: '/admin/customers', label: 'Customer Accounts' },
      ],
    },
  ];

  let currentMenuItems = customerMenuItems;
  if (activeRole === 'merchant') currentMenuItems = merchantMenuItems;
  if (activeRole === 'admin') currentMenuItems = adminMenuItems;

  // Selected key helper
  const selectedKeys = [location.pathname];


  const unreadCount = notifications.filter((n) => n.unread).length;
  const openNotification = (item: NotificationItem) => {
    if (item.action_url?.startsWith('/customer/')) navigate(item.action_url);
  };

  const notificationContent = (
    <div style={{ width: 340 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12, paddingBottom: 8, borderBottom: '1px solid #EBECF0' }}>
        <Text strong style={{ fontSize: 14 }}>Notifications</Text>
        <Button
          type="link"
          size="small"
          onClick={() => setNotifications(notifications.map((n) => ({ ...n, unread: false })))}
        >
          Mark all as read
        </Button>
      </div>
      <List
        size="small"
        itemLayout="horizontal"
        dataSource={notifications.slice(0, 4)}
        renderItem={(item) => (
          <List.Item
            onClick={() => openNotification(item)}
            style={{
              padding: '8px 4px',
              backgroundColor: item.unread ? '#F0F7FF' : 'transparent',
              borderRadius: 6,
              marginBottom: 4,
              cursor: item.action_url ? 'pointer' : 'default',
            }}
          >
            <List.Item.Meta
              avatar={
                <Avatar
                  size="small"
                  style={{
                    backgroundColor: item.type === 'offer' ? '#1677FF' : item.type === 'demand' ? '#52C41A' : '#722ED1',
                  }}
                  icon={item.type === 'offer' ? <TagsOutlined /> : item.type === 'demand' ? <TeamOutlined /> : <InboxOutlined />}
                />
              }
              title={<span style={{ fontSize: 13, fontWeight: item.unread ? 600 : 400 }}>{item.title}</span>}
              description={
                <div>
                  <div style={{ fontSize: 12, color: '#6B778C' }}>{item.description}</div>
                  <div style={{ fontSize: 10, color: '#8993A4', marginTop: 2 }}>{item.created_at}</div>
                </div>
              }
            />
          </List.Item>
        )}
      />
      <div style={{ textAlign: 'center', marginTop: 8, paddingTop: 8, borderTop: '1px solid #EBECF0' }}>
        <Button type="link" size="small" onClick={() => navigate('/customer/notifications')}>
          View all notifications
        </Button>
      </div>
    </div>
  );

  const profileMenuItems: MenuProps['items'] = [
    {
      key: 'dashboard',
      label: 'My Dashboard',
      onClick: () => navigate(`/${activeRole}/dashboard`),
    },
    { type: 'divider' },
    {
      key: 'profile-view',
      label: (
        <div>
          <div style={{ fontWeight: 600, color: '#172B4D' }}>{profileName}</div>
          <div style={{ fontSize: 12, color: '#6B778C' }}>{profileEmail}</div>
          <Tag color="blue" style={{ marginTop: 4 }}>Role: {activeRole?.toUpperCase() ?? 'UNVERIFIED'}</Tag>
        </div>
      ),
    },
    { type: 'divider' },
    {
      key: 'settings',
      icon: <SettingOutlined />,
      label: 'Account Preferences',
      onClick: () => navigate(`/${activeRole}/profile`),
    },
    { type: 'divider' },
    {
      key: 'logout',
      icon: <CloseCircleOutlined />,
      label: 'Logout',
      onClick: handleLogout,
    },
  ];

  return (
    <Layout style={{ minHeight: '100vh', background: '#F5F7FA' }}>
      {/* Sider Navigation */}
      {user && activeRole && !isHomePage && (
      <Sider
        collapsible
        collapsed={collapsed}
        onCollapse={(value) => setCollapsed(value)}
        width={280}
        theme="light"
        style={{
          borderRight: '1px solid #E2E8F0',
          position: 'sticky',
          top: 0,
          height: '100vh',
          zIndex: 100,
          background: '#FFFFFF',
        }}
      >
        {/* Brand Header */}
        <div
          style={{
            height: 72,
            display: 'flex',
            alignItems: 'center',
            padding: '0 22px',
            borderBottom: '1px solid #E2E8F0',
            cursor: 'pointer',
          }}
          onClick={() => navigate('/')}
        >
          <div
            style={{
              width: 38,
              height: 38,
              borderRadius: 12,
              background: 'linear-gradient(135deg, #4F46E5 0%, #7C3AED 100%)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              color: '#FFFFFF',
              fontWeight: 800,
              fontSize: 17,
              boxShadow: '0 2px 10px rgba(79, 70, 229, 0.35)',
              marginRight: collapsed ? 0 : 12,
              flexShrink: 0,
            }}
          >
            MT
          </div>
          {!collapsed && (
            <div>
              <div style={{ fontWeight: 800, fontSize: 18, color: '#0F172A', lineHeight: 1.2 }}>
                Market<span style={{ color: '#4F46E5' }}>Twin</span>
              </div>
              <div style={{ fontSize: 11, color: '#64748B', fontWeight: 600, letterSpacing: 0.5 }}>
                DEMAND-DRIVEN B2B
              </div>
            </div>
          )}
        </div>

        {/* Current Active Role Badge */}
        {!collapsed && (
          <div style={{ padding: '14px 18px 6px 18px' }}>
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                padding: '8px 12px',
                background: '#F8FAFC',
                borderRadius: 10,
                border: '1px solid #E2E8F0',
              }}
            >
              <span style={{ fontSize: 11.5, fontWeight: 700, color: '#64748B', textTransform: 'uppercase', letterSpacing: 0.5 }}>
                Active Module
              </span>
              <Tag
                color={activeRole === 'customer' ? 'blue' : activeRole === 'merchant' ? 'orange' : 'purple'}
                style={{ margin: 0, textTransform: 'capitalize', fontWeight: 700, borderRadius: 14, padding: '2px 10px', fontSize: 12 }}
              >
                {activeRole}
              </Tag>
            </div>
          </div>
        )}

        {/* Nav Menu */}
        <div
          className={
            activeRole === 'admin'
              ? 'admin-sidebar'
              : activeRole === 'merchant'
              ? 'merchant-sidebar'
              : 'customer-sidebar'
          }
          style={{
            overflowY: 'auto',
            height: !collapsed ? 'calc(100vh - 250px)' : 'calc(100vh - 140px)',
            paddingTop: 8,
          }}
        >
          <Menu
            mode="inline"
            selectedKeys={selectedKeys}
            items={currentMenuItems}
            onClick={({ key }) => {
              navigate(key);
            }}
            style={{ borderRight: 0 }}
          />
        </div>

        {/* Bottom Menu Items (Styled like reference SaaS navigation) */}
        {!collapsed && (
          <div
            style={{
              position: 'absolute',
              bottom: 0,
              left: 0,
              right: 0,
              padding: '10px 16px',
              borderTop: '1px solid #F1F5F9',
              background: '#FFFFFF',
            }}
          >
            <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
              <div
                onClick={() => {
                  if (activeRole === 'admin') navigate('/admin/dashboard');
                  else if (activeRole === 'merchant') navigate('/merchant/profile');
                  else navigate('/customer/profile');
                }}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 10,
                  fontSize: 12.5,
                  fontWeight: 500,
                  color: '#64748B',
                  padding: '6px 10px',
                  borderRadius: 8,
                  cursor: 'pointer',
                  transition: 'background 0.2s',
                }}
                onMouseEnter={(e) => (e.currentTarget.style.backgroundColor = '#F8FAFC')}
                onMouseLeave={(e) => (e.currentTarget.style.backgroundColor = 'transparent')}
              >
                <UserOutlined style={{ fontSize: 14 }} />
                <span>
                  {activeRole === 'admin' ? 'Admin Overview' : activeRole === 'merchant' ? 'Merchant Profile' : 'My Account'}
                </span>
              </div>
              <div
                onClick={() => navigate('/customer/notifications')}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 10,
                  fontSize: 12.5,
                  fontWeight: 500,
                  color: '#64748B',
                  padding: '6px 10px',
                  borderRadius: 8,
                  cursor: 'pointer',
                  transition: 'background 0.2s',
                }}
                onMouseEnter={(e) => (e.currentTarget.style.backgroundColor = '#F8FAFC')}
                onMouseLeave={(e) => (e.currentTarget.style.backgroundColor = 'transparent')}
              >
                <BellOutlined style={{ fontSize: 14 }} />
                <span>Notifications</span>
              </div>
              <div
                onClick={handleLogout}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 10,
                  fontSize: 12.5,
                  fontWeight: 500,
                  color: '#94A3B8',
                  padding: '6px 10px',
                  borderRadius: 8,
                  cursor: 'pointer',
                  transition: 'background 0.2s',
                }}
                onMouseEnter={(e) => (e.currentTarget.style.backgroundColor = '#F8FAFC')}
                onMouseLeave={(e) => (e.currentTarget.style.backgroundColor = 'transparent')}
              >
                <LogoutOutlined style={{ fontSize: 14 }} />
                <span>Sign Out</span>
              </div>
            </div>
          </div>
        )}
      </Sider>
      )}

      {/* Main Content Area */}
      <Layout style={{ background: '#F5F7FA' }}>
        {/* Top Header */}
        <Header
          style={{
            background: '#FFFFFF',
            borderBottom: '1px solid #E2E8F0',
            padding: isHomePage ? '0 24px' : '0 32px',
            height: 72,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            position: 'sticky',
            top: 0,
            zIndex: 90,
          }}
        >
          {/* Left: Home brand or Sidebar Collapse Toggle on Internal Pages */}
          <Space orientation="horizontal" size={16} align="center">
            {isHomePage ? (
              <div
                style={{
                  display: 'flex',
                  flexDirection: 'column',
                  alignItems: 'flex-start',
                  maxWidth: '52vw',
                }}
              >
                <span
                  style={{
                    color: '#0F172A',
                    fontSize: 19,
                    fontWeight: 800,
                    lineHeight: 1.15,
                  }}
                >
                  MarketTwin
                </span>
                <span
                  style={{
                    color: '#64748B',
                    fontSize: 12,
                    fontWeight: 600,
                    lineHeight: 1.4,
                  }}
                >
                  AI-Powered Product Discovery for Businesses
                </span>
              </div>
            ) : (
              user && (
                <Button
                  type="text"
                  icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
                  onClick={() => setCollapsed(!collapsed)}
                  style={{ fontSize: 18, color: '#475569' }}
                />
              )
            )}
          </Space>

          {!isHomePage && (
            <div style={{ flex: '0 1 460px', margin: '0 20px' }} className="hidden md:block">
              <Input
                placeholder="Search across the app..."
                prefix={<SearchOutlined style={{ color: '#94A3B8', marginRight: 8, fontSize: 15 }} />}
                suffix={
                  <span
                    style={{
                      fontSize: 11.5,
                      backgroundColor: '#F1F5F9',
                      color: '#64748B',
                      padding: '2px 8px',
                      borderRadius: 6,
                      border: '1px solid #E2E8F0',
                      fontWeight: 600,
                    }}
                  >
                    ⌘/
                  </span>
                }
                style={{
                  borderRadius: 24,
                  backgroundColor: '#F8FAFC',
                  border: '1px solid #E2E8F0',
                  height: 42,
                  fontSize: 14,
                  padding: '0 16px',
                }}
              />
            </div>
          )}

          {/* Right: Switch Role + Backend Indicator + Bell + User Icon + Avatar + Account ▾ */}
          <Space orientation="horizontal" size={14} align="center">
            {user && profile && activeRole ? (
              <>
                <Tag
                  color={activeRole === 'customer' ? 'blue' : activeRole === 'merchant' ? 'orange' : 'purple'}
                  style={{ textTransform: 'capitalize', fontSize: 12.5, padding: '3px 12px', borderRadius: 14, fontWeight: 600 }}
                >
                  {activeRole}
                </Tag>

                {/* Notifications Popover */}
                <Popover content={notificationContent} trigger="click" placement="bottomRight">
                  <Badge count={unreadCount} size="small" offset={[-2, 4]}>
                    <Button type="text" shape="circle" icon={<BellOutlined style={{ fontSize: 19, color: '#475569' }} />} />
                  </Badge>
                </Popover>

                {/* User Silhouette Icon */}
                <Button
                  type="text"
                  shape="circle"
                  icon={<UserOutlined style={{ fontSize: 18, color: '#475569' }} />}
                  onClick={() => navigate(`/${activeRole}/profile`)}
                />

                {/* User Avatar Circle */}
                <Avatar
                  src={profile.avatarUrl || undefined}
                  alt={profileName}
                  style={{ backgroundColor: '#4F46E5', verticalAlign: 'middle', fontWeight: 600 }}
                  size={38}
                >
                  {profileInitials(profile.fullName, profile.email)}
                </Avatar>

                {/* Account ▾ Dropdown Pill */}
                <Dropdown menu={{ items: profileMenuItems }} placement="bottomRight">
                  <button
                    style={{
                      display: 'inline-flex',
                      alignItems: 'center',
                      gap: 8,
                      background: '#FFFFFF',
                      border: '1px solid #E2E8F0',
                      borderRadius: 20,
                      padding: '6px 14px',
                      fontSize: 13.5,
                      fontWeight: 600,
                      color: '#334155',
                      cursor: 'pointer',
                      height: 40,
                    }}
                  >
                    <span>Account</span>
                    <DownOutlined style={{ fontSize: 10, color: '#94A3B8' }} />
                  </button>
                </Dropdown>
              </>
            ) : (
              <>
                <Button type="text" onClick={() => openRoleSelection('login')} style={{ fontSize: 14, fontWeight: 600 }}>Login</Button>
                <Button type="primary" onClick={() => openRoleSelection('register')} style={{ fontSize: 14, fontWeight: 600, height: 40, borderRadius: 20, padding: '0 20px' }}>Register</Button>
              </>
            )}
          </Space>
        </Header>

        <RoleSelectionModal
          open={roleSelectionOpen}
          action={authAction}
          onClose={closeRoleSelection}
          onSelectRole={navigateForRole}
        />

        {/* Page Content Body */}
        <Content
          style={{
            margin: '28px 32px 32px 32px',
            minHeight: 'calc(100vh - 190px)',
            background: 'transparent',
          }}
        >
          <Outlet key={user?.id ?? 'anonymous'} context={{ isBackendHealthy, healthData, checkHealth }} />
        </Content>

        {/* Light Footer */}
        <Footer
          style={{
            background: '#FFFFFF',
            borderTop: '1px solid #E2E8F0',
            textAlign: 'center',
            padding: '20px 32px',
            color: '#64748B',
            fontSize: 14,
          }}
        >
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 12 }}>
            <div>
              <Text strong style={{ color: '#172B4D', fontSize: 14.5 }}>MarketTwin</Text> &mdash; Demand-Driven B2B Marketplace Platform
            </div>
            <div style={{ fontSize: 13, color: '#94A3B8' }}>
              &copy; {new Date().getFullYear()} MarketTwin Inc. All rights reserved.
            </div>
          </div>
        </Footer>
      </Layout>
    </Layout>
  );
};

export default AppLayout;
