import { StrictMode, useEffect } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import { RouterProvider, useLocation, useNavigationType, createRoutesFromChildren, matchRoutes } from 'react-router'
import { router } from './routes'
import * as Sentry from "@sentry/react";
import { ConfigProvider } from 'antd';

// 声明 Vite 注入的全局变量
declare const __APP_VERSION__: string;
declare const __APP_NAME__: string;

Sentry.init({
	dsn: "https://115312e36450246c6f2cab50c5432314@o4510499341008896.ingest.us.sentry.io/4510499342385152",
	// 仅在生产环境下启用 Sentry
	enabled: import.meta.env.PROD,
	sendDefaultPii: true,
	// 使用与构建时一致的 Release 名称
	release: `${__APP_NAME__}@${__APP_VERSION__}`,
	integrations: [
		Sentry.reactRouterV6BrowserTracingIntegration({
			useEffect,
			useLocation,
			useNavigationType,
			createRoutesFromChildren,
			matchRoutes,
		}),
		Sentry.replayIntegration(),
	],
	// 生产环境建议调整为 0.1 或更低
	tracesSampleRate: 1.0,
	// 生产环境建议调整为 0.1
	replaysSessionSampleRate: 0.1,
	replaysOnErrorSampleRate: 1.0,
});

const container = document.getElementById('root');

createRoot(container!).render(
	<StrictMode>
		<ConfigProvider theme={{ token: { fontFamily: "'Open Sans', sans-serif" } }}>
			<RouterProvider router={router} />
		</ConfigProvider>
	</StrictMode>
)
