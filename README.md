# MarketTwin

MarketTwin is a demand-driven marketplace that helps customers discover products, express buying intent when a product is unavailable, and connect with merchants through grouped demand pools. The platform combines catalogue discovery, AI-assisted requirement extraction, demand aggregation, merchant bidding, offer validation, and checkout flows.

This repository contains the backend API and the React + Vite frontend for the MarketTwin experience.

## Project Goals

- Aggregate similar customer intents into a single actionable demand group
- Let merchants review grouped demand and submit competitive offers
- Validate merchant offers before a customer selects a winner
- Support production-style order flows and PayPal sandbox checkout
- Offer admin visibility into demands, offers, orders, and platform health

## Tech Stack

### Backend
- Python 3.10+
- FastAPI
- Pydantic v2
- Supabase Python client
- Uvicorn
- Groq and Ollama-compatible AI providers

### Frontend
- React 18
- TypeScript
- Vite
- Tailwind CSS
- Ant Design
- React Router

### Data and platform services
- Supabase PostgreSQL
- Supabase Auth
- Supabase Storage
- Realtime subscriptions
- PayPal Sandbox integration

## Repository Structure

```text
PayPal-Tharunika/
├── README.md
├── docs/
│   └── DATABASE.md
├── backend/
│   ├── .env
│   ├── requirements.txt
│   ├── app/
│   │   ├── api/
│   │   ├── core/
│   │   ├── main.py
│   │   ├── schemas/
│   │   └── services/
│   ├── scripts/
│   ├── sql/
│   └── tests/
├── frontend/
│   ├── .env
│   ├── package.json
│   ├── vite.config.ts
│   ├── public/
│   └── src/
└── .gitignore
```

## Prerequisites

- Python 3.10 or newer
- Node.js 18+ and npm
- Git
- Access to a Supabase project
- PayPal Sandbox credentials for checkout testing

## Backend Setup

1. Create and activate a virtual environment:

```bash
cd backend
python -m venv .venv
```

On Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

On macOS/Linux:

```bash
source .venv/bin/activate
```

2. Install Python dependencies:

```bash
pip install -r requirements.txt
```

3. Create `backend/.env` with the required settings. Example:

```env
PROJECT_NAME="MarketTwin API"
ENVIRONMENT="development"
DEBUG=True
API_V1_STR="/api/v1"
ALLOWED_ORIGINS="http://localhost:5173,http://127.0.0.1:5173"

SUPABASE_URL="https://<project-ref>.supabase.co"
SUPABASE_KEY="<supabase-anon-key>"
SUPABASE_SERVICE_ROLE_KEY="<supabase-service-role-key>"
SUPABASE_JWT_SECRET="<jwt-secret>"
FRONTEND_URL="http://localhost:5173"

AI_PROVIDER="groq"
GROQ_API_KEY="<your-key>"
GROQ_BASE_URL="https://api.groq.com/openai/v1"
GROQ_MODEL="openai/gpt-oss-20b"

PAYPAL_CLIENT_ID="<sandbox-client-id>"
PAYPAL_CLIENT_SECRET="<sandbox-client-secret>"
PAYPAL_MODE="sandbox"
```

4. Start the API:

```bash
cd backend
python -m uvicorn app.main:app --reload --port 8000
```

The API is available at:
- http://localhost:8000
- http://localhost:8000/docs
- http://localhost:8000/api/v1/health

## Frontend Setup

1. Install frontend dependencies:

```bash
cd frontend
npm install
```

2. Create `frontend/.env`:

```env
VITE_API_BASE_URL=http://localhost:8000
VITE_SUPABASE_URL=https://<project-ref>.supabase.co
VITE_SUPABASE_ANON_KEY=<supabase-anon-key>
```

3. Run the app:

```bash
npm run dev
```

The frontend will usually run on:
- http://localhost:5173

## Database and Migrations

Database schema and migration files live under:

- `backend/sql/`
- `backend/sql/migrations/`

The repo documentation in `docs/DATABASE.md` explains the schema, migration expectations, and Supabase setup. Review those files before applying migrations to staging or production.

Important:
- Stable migrations should be applied in numeric order.
- Draft or unapproved SQL files must be reviewed carefully before they are used in a live database.
- `DEMAND_PERSISTENCE_ENABLED` and related feature flags should be treated as guarded settings until staging validation is complete.

## Core Features

- Product catalogue and search
- Customer demand creation and review
- AI-assisted demand requirement extraction
- Similar demand grouping and merchant visibility
- Merchant offer submission and validation
- Offer selection and order creation
- PayPal sandbox payment flows
- Fulfillment tracking and admin monitoring

## Testing

### Backend

```bash
cd backend
python -m pytest -v
```

### Frontend

```bash
cd frontend
npm run build
```

## Security Notes

- Keep Supabase service-role credentials on the backend only.
- Do not expose PayPal secrets in frontend code or public environment files.
- Use a sandbox account for PayPal testing before switching to live credentials.
- Validate RLS policies and access rules before any production deployment.

## Notes

This project is built for local development and staging validation. Production deployment requires reviewing database migrations, environment settings, and payment configuration before exposing the app to real users.
