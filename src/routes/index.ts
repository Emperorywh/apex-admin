import { createBrowserRouter, type RouteObject } from 'react-router';
import App from '@/App';
import Login from '@/pages/login';
import type { AppRouteObject } from '@/types/router';
import { routeChildren } from './config';

export const routes: AppRouteObject[] = [
	{
		path: '/',
		Component: App,
		handle: {
			title: '首页',
			icon: 'UserOutlined',
		},
		children: routeChildren,
	},
	{
		path: 'login',
		Component: Login,
		handle: { title: '登录' },
	},
];

export const router = createBrowserRouter(routes as RouteObject[]);
