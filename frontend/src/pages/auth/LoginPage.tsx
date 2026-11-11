import React, { useState } from 'react';
import { useNavigate, useLocation, useSearchParams, Link } from 'react-router-dom';
import { Card, Form, Input, Button, Typography, Alert, Space } from 'antd';
import { MailOutlined, LockOutlined } from '@ant-design/icons';
import { useAuth } from '../../context/AuthContext';
import { supabase } from '../../services/supabase';
import {
  authReturnState,
  safeAuthReturnLocation,
} from '../../utils/authReturnLocation';

const { Title, Text } = Typography;

const LoginPage: React.FC = () => {
  const navigate = useNavigate();
  const location = useLocation();
  const [searchParams] = useSearchParams();
  const { signIn, signOut } = useAuth();
  const requestedRole = searchParams.get('role');
  const selectedRole = requestedRole === 'merchant' ? 'merchant' : 'customer';
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const onFinish = async (values: { email: string; password: string }) => {
    setLoading(true);
    setError(null);

    const { error: err } = await signIn(values.email, values.password);
    if (err) {
      setLoading(false);
      setError(err);
      return;
    }

    if (!supabase) {
      setLoading(false);
      setError('Supabase is not configured.');
      return;
    }

    const { data: userData, error: userError } = await supabase.auth.getUser();
    if (userError) {
      await signOut();
      setLoading(false);
      setError('Unable to verify authenticated user.');
      return;
    }
    const userId = userData.user?.id;
    if (!userId) {
      await signOut();
      setLoading(false);
      setError('Unable to resolve authenticated user.');
      return;
    }

    const { data: profile, error: profileError } = await supabase
      .from('profiles')
      .select('role')
      .eq('id', userId)
      .single();
    if (profileError) {
      await signOut();
      setLoading(false);
      setError('Unable to verify this account role. Please try again.');
      return;
    }
    const actualRole = profile?.role as string | undefined;

    if (actualRole !== selectedRole) {
      await signOut();
      setLoading(false);
      setError(`This account is not registered as a ${selectedRole}.`);
      return;
    }

    if (actualRole === 'customer' && !userData.user?.email_confirmed_at) {
      await signOut();
      setLoading(false);
      setError('Verify your email address before signing in.');
      return;
    }

    setLoading(false);
    const returnLocation = safeAuthReturnLocation(location.state);
    const target = actualRole === 'customer' && returnLocation
      ? `${returnLocation.pathname}${returnLocation.search}${returnLocation.hash}`
      : actualRole === 'merchant'
        ? '/merchant/dashboard'
        : '/customer/dashboard';
    navigate(target, { replace: true });
  };

  return (
    <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', background: '#F5F7FA', padding: 24 }}>
      <Card style={{ width: 440, boxShadow: '0 8px 24px rgba(0,0,0,0.08)' }}>
        <Space orientation="vertical" size={4} style={{ width: '100%', marginBottom: 16 }}>
          <Title level={3} style={{ margin: 0, color: '#172B4D' }}>Sign in to MarketTwin</Title>
          <Text type="secondary">Choose your role and use your Supabase account.</Text>
        </Space>
        {error && <Alert type="error" title={error} style={{ marginBottom: 16 }} />}
        <Form layout="vertical" onFinish={onFinish}>
          <Form.Item name="email" label="Email" rules={[{ required: true, type: 'email' }]}>
            <Input prefix={<MailOutlined />} />
          </Form.Item>
          <Form.Item name="password" label="Password" rules={[{ required: true }]}>
            <Input.Password prefix={<LockOutlined />} />
          </Form.Item>
          <Form.Item>
            <Button type="primary" htmlType="submit" block loading={loading}>
              Sign In as {selectedRole}
            </Button>
          </Form.Item>
        </Form>
        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 13 }}>
          <Link to="/auth/forgot-password">Forgot password?</Link>
          <Link
            to="/auth/register/customer"
            state={authReturnState(
              safeAuthReturnLocation(location.state) || {
                pathname: '/customer/dashboard',
                search: '',
                hash: '',
              }
            )}
          >
            Create account
          </Link>
        </div>
      </Card>
    </div>
  );
};

export default LoginPage;