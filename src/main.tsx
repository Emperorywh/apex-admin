import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import { RouterProvider } from 'react-router'
import { router } from './routes'
import * as Sentry from "@sentry/react";

Sentry.init({
  dsn: "https://115312e36450246c6f2cab50c5432314@o4510499341008896.ingest.us.sentry.io/4510499342385152",
  sendDefaultPii: true
});

const container = document.getElementById('root');

createRoot(container!).render(
  <StrictMode>
    <RouterProvider router={router} />
  </StrictMode>
)
