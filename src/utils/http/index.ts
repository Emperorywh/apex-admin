import axios, { type AxiosInstance, type AxiosResponse, type InternalAxiosRequestConfig } from 'axios';
import { message, Modal } from 'antd';
import * as Sentry from "@sentry/react";
import { AxiosCanceler } from './axiosCancel';
import type { RequestOptions } from './types';
import { getToken, setToken, clearAuth } from '../auth';

// 重新请求队列
let requestQueue: Array<(token: string) => void> = [];
// 是否正在刷新 token
let isRefreshing = false;

const axiosCanceler = new AxiosCanceler();

// Default request options
const defaultRequestOptions: RequestOptions = {
	joinParamsToUrl: true,
	isTransformResponse: true,
	isReturnNativeResponse: false,
	ignoreCancelToken: false,
	withToken: true,
	errorMessageMode: 'message',
};

// Create Axios instance
const service: AxiosInstance = axios.create({
	timeout: 10 * 1000,
	headers: { 'Content-Type': 'application/json;charset=UTF-8' },
});

// Request Interceptor
service.interceptors.request.use(
	(config: InternalAxiosRequestConfig) => {
		// Merge options
		const options = { ...defaultRequestOptions, ...config.requestOptions };
		config.requestOptions = options;

		// Handle cancel token
		if (!options.ignoreCancelToken) {
			axiosCanceler.addPending(config);
		}

		// Add token
		if (options.withToken) {
			const token = getToken();
			if (token && config.headers) {
				config.headers.Authorization = `Bearer ${token}`;
			}
		}

		// Handle form-data (simplified)
		const contentType = config.headers?.['Content-Type'] || config.headers?.['content-type'];
		if (contentType === 'application/x-www-form-urlencoded;charset=UTF-8' && config.data && typeof config.data === 'object') {
			config.data = new URLSearchParams(config.data).toString();
		}

		return config;
	},
	(error) => {
		return Promise.reject(error);
	}
);

// Response Interceptor
service.interceptors.response.use(
	(response: AxiosResponse<unknown>) => {
		const config = response.config;
		const options = config.requestOptions || defaultRequestOptions;

		// Remove pending request
		axiosCanceler.removePending(config);

		// Return native response
		if (options.isReturnNativeResponse) {
			return response;
		}

		// Return data directly if no transform needed
		if (!options.isTransformResponse) {
			return response.data;
		}

		const { data } = response;
		if (!data) {
			throw new Error('请求出错，请稍后重试');
		}

		// Handle standard response structure { code, data, message }
		// Adjust these fields based on your actual backend response structure
		const { code, data: result, message: msg } = data;

		// Success (Assuming 0 is success)
		if (code === 0) {
			return result;
		}

		// Business Error
		const errorMsg = msg || '请求出错，请稍后重试';

		// Handle specific codes
		if (code === 401) {
			handle401();
		} else {
			showError(errorMsg, options.errorMessageMode);
		}

		// Report business error to Sentry
		reportSentry(response, 'api_business_error', code, errorMsg);

		throw new Error(errorMsg);
	},
	(error) => {
		const { response, config } = error || {};

		if (config) {
			axiosCanceler.removePending(config);
		}

		// Report HTTP error to Sentry
		if (response?.status !== 401) {
			reportSentry(error, 'api_http_error');
		}

		const errorMessageMode = config?.requestOptions?.errorMessageMode || 'none';
		let errMessage = response?.data?.message || error?.message || '请求出错，请稍后重试';

		if (error.code === 'ECONNABORTED' && error.message.includes('timeout')) {
			errMessage = '接口请求超时，请刷新页面重试!';
		} else if (error?.message?.includes('Network Error')) {
			errMessage = '网络异常，请检查您的网络连接是否正常!';
		}

		// Handle Status Codes
		const status = response?.status;
		switch (status) {
			case 401:
				// 如果是刷新 token 的请求本身 401 了，那只能跳登录页了
				if (config.url.includes('/refresh-token')) {
					handle401();
					break;
				}

				if (!isRefreshing) {
					isRefreshing = true;
					// 模拟刷新 token
					return new Promise((resolve) => {
						// 这里替换为真实的 refresh token 请求
						// simulateRefreshToken()
						const newToken = 'simulated_new_token_' + Date.now();

						setTimeout(() => {
							setToken(newToken);
							isRefreshing = false;

							// 重发队列中的请求
							requestQueue.forEach(cb => cb(newToken));
							requestQueue = [];

							// 重发当前请求
							config.headers.Authorization = `Bearer ${newToken}`;
							// 使用 service.request 重发请求
							resolve(service.request(config));
						}, 1000); // 模拟耗时
					});
				} else {
					// 正在刷新，加入队列
					return new Promise((resolve) => {
						requestQueue.push((token) => {
							config.headers.Authorization = `Bearer ${token}`;
							resolve(service.request(config));
						});
					});
				}
			case 403:
				errMessage = '用户得到授权，但是访问是被禁止的。';
				break;
			case 404:
				errMessage = '网络请求错误,未找到该资源!';
				break;
			case 500:
				errMessage = '服务器错误,请联系管理员!';
				break;
		}

		if (status !== 401) {
			showError(errMessage, errorMessageMode);
		}

		return Promise.reject(error);
	}
);

function handle401() {
	clearAuth();
	window.location.href = '/login';
}

function showError(msg: string, mode: 'none' | 'modal' | 'message' | undefined) {
	if (mode === 'modal') {
		Modal.error({ title: '错误提示', content: msg });
	} else if (mode === 'message') {
		message.error(msg);
	}
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function reportSentry(data: any, type: string, code?: number, msg?: string) {
	Sentry.withScope((scope) => {
		scope.setTag("type", type);
		const config = data.config || data.response?.config;
		if (config) {
			scope.setExtra("url", config.url);
			scope.setExtra("method", config.method);
			scope.setExtra("params", config.params);
			scope.setExtra("data", config.data);
		}
		if (data.status) {
			scope.setExtra("status", data.status);
		}
		if (code) scope.setExtra("business_code", code);
		if (msg) scope.setExtra("business_message", msg);

		if (type === 'api_business_error') {
			Sentry.captureMessage(`API Business Error: ${config?.url} (${code})`, "warning");
		} else {
			Sentry.captureException(data);
		}
	});
}

export default service;
