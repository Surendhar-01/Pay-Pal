import React from 'react';
import { createBrowserRouter, Navigate } from 'react-router-dom';
import AppLayout from '../components/layout/AppLayout';
import ProtectedRoute from '../components/common/ProtectedRoute';

// Root Pages
import HomePage from '../pages/HomePage';
import NotFoundPage from '../pages/NotFoundPage';

// Auth Pages
import LoginPage from '../pages/auth/LoginPage';
import RegisterCustomerPage from '../pages/auth/RegisterCustomerPage';
import RegisterMerchantPage from '../pages/auth/RegisterMerchantPage';
import VerifyEmailPage from '../pages/auth/VerifyEmailPage';
import UnauthorizedPage from '../pages/UnauthorizedPage';
import ForgotPasswordPage from '../pages/auth/ForgotPasswordPage';
import ResetPasswordPage from '../pages/auth/ResetPasswordPage';
import MerchantVerificationStatusPage from '../pages/auth/MerchantVerificationStatusPage';

// Customer Module
import CustomerDashboard from '../pages/customer/CustomerDashboard';
import ProductCatalogue from '../pages/customer/ProductCatalogue';
import ProductSearch from '../pages/customer/ProductSearch';
import ProductDetails from '../pages/customer/ProductDetails';
import CreateDemand from '../pages/customer/CreateDemand';
import DemandAnalysis from '../pages/customer/DemandAnalysis';
import MyDemands from '../pages/customer/MyDemands';
import DemandGroups from '../pages/customer/DemandGroups';
import MerchantOffers from '../pages/customer/MerchantOffers';
import OfferComparison from '../pages/customer/OfferComparison';
import Orders from '../pages/customer/Orders';
import OrderTracking from '../pages/customer/OrderTracking';
import NotificationsPage from '../pages/customer/NotificationsPage';
import CustomerProfile from '../pages/customer/CustomerProfile';
import {
  CheckoutPage,
  PaymentCancelledPage,
  PaymentFailurePage,
  PaymentSuccessPage,
} from '../pages/customer/PayPalCheckoutPages';

// Merchant Module
import MerchantDashboard from '../pages/merchant/MerchantDashboard';
import AvailableDemands from '../pages/merchant/AvailableDemands';
import DemandDetails from '../pages/merchant/DemandDetails';
import SubmitOffer from '../pages/merchant/SubmitOffer';
import MyOffers from '../pages/merchant/MyOffers';
import MerchantOrders from '../pages/merchant/MerchantOrders';
import MerchantProfile from '../pages/merchant/MerchantProfile';

const C = (node: React.ReactNode, roles?: Array<'customer' | 'merchant'>) => (
  <ProtectedRoute allowedRoles={roles}>{node}</ProtectedRoute>
);

export const router = createBrowserRouter([
  {
    path: '/',
    element: <AppLayout />,
    children: [
      { index: true, element: <HomePage /> },
      { path: 'customer', element: <Navigate to="/customer/dashboard" replace /> },
      { path: 'customer/dashboard', element: C(<CustomerDashboard />, ['customer']) },
      { path: 'customer/catalogue', element: C(<ProductCatalogue />, ['customer']) },
      { path: 'customer/search', element: C(<ProductSearch />, ['customer']) },
      { path: 'customer/products/:id', element: C(<ProductDetails />, ['customer']) },
      { path: 'customer/demands', element: C(<MyDemands />, ['customer']) },
      { path: 'customer/demands/create', element: C(<CreateDemand />, ['customer']) },
      { path: 'customer/demands/:demandId/analysis', element: C(<DemandAnalysis />, ['customer']) },
      { path: 'customer/groups', element: C(<DemandGroups />, ['customer']) },
      { path: 'customer/offers', element: C(<MerchantOffers />, ['customer']) },
      { path: 'customer/compare', element: C(<OfferComparison />, ['customer']) },
      { path: 'customer/orders', element: C(<Orders />, ['customer']) },
      { path: 'customer/orders/:id/track', element: C(<OrderTracking />, ['customer']) },
      { path: 'customer/notifications', element: C(<NotificationsPage />, ['customer']) },
      { path: 'customer/profile', element: C(<CustomerProfile />, ['customer']) },
      { path: 'customer/checkout', element: C(<CheckoutPage />, ['customer']) },
      { path: 'customer/payment/success', element: C(<PaymentSuccessPage />, ['customer']) },
      { path: 'customer/payment/cancelled', element: C(<PaymentCancelledPage />, ['customer']) },
      { path: 'customer/payment/failure', element: C(<PaymentFailurePage />, ['customer']) },

      { path: 'merchant', element: <Navigate to="/merchant/dashboard" replace /> },
      { path: 'merchant/dashboard', element: C(<MerchantDashboard />, ['merchant']) },
      { path: 'merchant/demands', element: C(<AvailableDemands />, ['merchant']) },
      { path: 'merchant/demands/:id', element: C(<DemandDetails />, ['merchant']) },
      { path: 'merchant/offers/submit', element: C(<SubmitOffer />, ['merchant']) },
      { path: 'merchant/offers', element: C(<MyOffers />, ['merchant']) },
      { path: 'merchant/orders', element: C(<MerchantOrders />, ['merchant']) },
      { path: 'merchant/profile', element: C(<MerchantProfile />, ['merchant']) },
      { path: 'merchant/verification-status', element: C(<MerchantVerificationStatusPage />, ['merchant']) },

      { path: '*', element: <NotFoundPage /> },
    ],
  },
  { path: '/login', element: <LoginPage /> },
  { path: '/register', element: <RegisterCustomerPage /> },
  { path: '/register/customer', element: <RegisterCustomerPage /> },
  { path: '/register/merchant', element: <RegisterMerchantPage /> },
  { path: '/unauthorized', element: <UnauthorizedPage /> },
  { path: '/auth/login', element: <LoginPage /> },
  { path: '/auth/register/customer', element: <RegisterCustomerPage /> },
  { path: '/auth/register/merchant', element: <RegisterMerchantPage /> },
  { path: '/auth/verify-email', element: <VerifyEmailPage /> },
  { path: '/auth/forgot-password', element: <ForgotPasswordPage /> },
  { path: '/auth/reset-password', element: <ResetPasswordPage /> },
], {
  future: {
    v7_relativeSplatPath: true,
  },
});

export default router;