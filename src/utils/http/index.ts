import axios, { type AxiosInstance, type AxiosRequestConfig, type AxiosResponse, type InternalAxiosRequestConfig } from 'axios';
import { message, Modal } from 'antd';
import * as Sentry from "@sentry/react";
import { cloneDeep } from 'lodash-es';
import { AxiosCanceler } from './axiosCancel';
import type { CreateAxiosOptions, RequestOptions, Result } from './types';
import { getToken, setToken, clearAuth } from '../auth';

// 重新请求队列
let requestQueue: Array<(token: string) => void> = [];
// 是否正在刷新 token
let isRefreshing = false;

const axiosCanceler = new AxiosCanceler();

class VAxios {
	private axiosInstance: AxiosInstance;
	private readonly options: CreateAxiosOptions;

	constructor(options: CreateAxiosOptions) {
		this.options = options;
		this.axiosInstance = axios.create(options);
		this.setupInterceptors();
	}

	private getTransform() {
		const { transform } = this.options;
		return transform;
	}

	private setupInterceptors() {
		const transform = this.getTransform();
		if (!transform) {
			return;
		}
		const {
			requestInterceptors,
			requestInterceptorsCatch,
			responseInterceptors,
			responseInterceptorsCatch,
		} = transform;

		// 请求拦截器
		this.axiosInstance.interceptors.request.use((config: InternalAxiosRequestConfig) => {
			// 如果开启取消重复请求，则在这里处理
			// @ts-ignore
			const { ignoreCancelToken } = config;
			const ignoreCancel = ignoreCancelToken !== undefined ? ignoreCancelToken : this.options.requestOptions?.ignoreCancelToken;

			!ignoreCancel && axiosCanceler.addPending(config);

			if (requestInterceptors) {
				config = requestInterceptors(config as any, this.options) as InternalAxiosRequestConfig;
			}
			return config;
		}, undefined);

		// 请求错误处理
		requestInterceptorsCatch &&
			this.axiosInstance.interceptors.request.use(undefined, requestInterceptorsCatch);

		// 响应拦截器
		this.axiosInstance.interceptors.response.use((res: AxiosResponse<any>) => {
			res && axiosCanceler.removePending(res.config);
			if (responseInterceptors) {
				res = responseInterceptors(res);
			}
			return res;
		}, undefined);

		// 响应错误处理
		responseInterceptorsCatch &&
			this.axiosInstance.interceptors.response.use(undefined, responseInterceptorsCatch);
	}

	// 支持 form-data
	supportFormData(config: AxiosRequestConfig) {
		const headers = config.headers || this.options.headers;
		const contentType = headers?.['Content-Type'] || headers?.['content-type'];

		if (
			contentType !== 'application/x-www-form-urlencoded;charset=UTF-8' ||
			!Reflect.has(config, 'data') ||
			config.method?.toUpperCase() === 'GET'
		) {
			return config;
		}

		return {
			...config,
			data: new URLSearchParams(config.data).toString(),
		};
	}

	get<T = any>(config: AxiosRequestConfig, options?: RequestOptions): Promise<T> {
		return this.request({ ...config, method: 'GET' }, options);
	}

	post<T = any>(config: AxiosRequestConfig, options?: RequestOptions): Promise<T> {
		return this.request({ ...config, method: 'POST' }, options);
	}

	put<T = any>(config: AxiosRequestConfig, options?: RequestOptions): Promise<T> {
		return this.request({ ...config, method: 'PUT' }, options);
	}

	delete<T = any>(config: AxiosRequestConfig, options?: RequestOptions): Promise<T> {
		return this.request({ ...config, method: 'DELETE' }, options);
	}

	request<T = any>(config: AxiosRequestConfig, options?: RequestOptions): Promise<T> {
		let conf: CreateAxiosOptions = cloneDeep(config);
		const transform = this.getTransform();

		const { requestOptions } = this.options;

		const opt: RequestOptions = Object.assign({}, requestOptions, options);

		const { beforeRequestHook, requestCatchHook, transformRequestHook } = transform || {};

		if (beforeRequestHook) {
			conf = beforeRequestHook(conf, opt);
		}

		conf.requestOptions = opt;

		conf = this.supportFormData(conf);

		return new Promise((resolve, reject) => {
			this.axiosInstance
				.request<any, AxiosResponse<Result>>(conf)
				.then((res: AxiosResponse<Result>) => {
					if (transformRequestHook) {
						try {
							const ret = transformRequestHook(res, opt);
							resolve(ret);
						} catch (err) {
							reject(err || new Error('request error!'));
						}
						return;
					}
					resolve(res as unknown as T);
				})
				.catch((e: Error) => {
					if (requestCatchHook) {
						reject(requestCatchHook(e, opt));
						return;
					}
					reject(e);
				});
		});
	}
}

// 默认配置
const defaultOptions: CreateAxiosOptions = {
	timeout: 10 * 1000,
	headers: { 'Content-Type': 'application/json;charset=UTF-8' },
	// 数据处理方式
	transform: {
		// 请求前处理 config
		beforeRequestHook: (config, options) => {
			const { withToken } = options;

			if (withToken !== false) {
				// 可以在这里添加 token
				const token = getToken();
				if (token && config.headers) {
					config.headers.Authorization = `Bearer ${token}`;
				}
			}
			return config;
		},

		// 处理响应数据
		transformRequestHook: (res: AxiosResponse<Result>, options: RequestOptions) => {
			const { isTransformResponse, isReturnNativeResponse } = options;
			// 是否返回原生响应头 比如：需要获取响应头时使用该属性
			if (isReturnNativeResponse) {
				return res;
			}
			// 不进行任何处理，直接返回
			// 用于页面代码可能需要直接获取 code，data，message 这些信息的时候开启
			if (!isTransformResponse) {
				return res.data;
			}

			const { data } = res;
			if (!data) {
				// return '[HTTP] Request has no return value';
				throw new Error('请求出错，请稍后重试');
			}
			// 这里 code，data，message 为 后台统一的字段
			const { code, data: result, message: msg } = data;

			// 这里逻辑可以根据项目进行修改
			const hasSuccess = data && code === 0; // 假设 0 是成功
			if (hasSuccess) {
				return result;
			}

			// Sentry 上报业务异常 (API请求成功，但业务逻辑失败)
			Sentry.withScope((scope) => {
				scope.setTag("type", "api_business_error");
				scope.setExtra("url", res.config.url);
				scope.setExtra("params", res.config.params);
				scope.setExtra("data", res.config.data);
				scope.setExtra("response_code", code);
				scope.setExtra("response_message", msg);
				Sentry.captureMessage(`API Business Error: ${res.config.url} (${code})`, "warning");
			});

			// 在这里统一处理业务异常
			let timeoutMsg = '';
			switch (code) {
				case 401:
					timeoutMsg = '登录超时，请重新登录';
					// 一般在这里不做跳转，交给 responseInterceptorsCatch 处理 401
					break;
				default:
					if (msg) {
						timeoutMsg = msg;
					}
			}

			// errorMessageMode=‘modal’的时候会显示modal错误弹窗，而不是消息提示，用于一些比较重要的错误
			// errorMessageMode='none' 一般是调用时明确表示不希望自动弹出错误提示
			if (options.errorMessageMode === 'modal') {
				Modal.error({ title: '错误提示', content: timeoutMsg });
			} else if (options.errorMessageMode === 'message') {
				message.error(timeoutMsg);
			}

			throw new Error(timeoutMsg || '请求出错，请稍后重试');
		},

		// 响应错误拦截
    responseInterceptorsCatch: (error: any) => {
      const { response, config } = error || {};

      // Sentry 上报 HTTP 网络/服务器异常
      Sentry.withScope((scope) => {
          scope.setTag("type", "api_http_error");
          if (config) {
              scope.setExtra("url", config.url);
              scope.setExtra("method", config.method);
              scope.setExtra("params", config.params);
              // 注意：敏感数据（如密码）不应上报，此处仅作演示
              scope.setExtra("data", config.data); 
          }
          if (response) {
              scope.setExtra("status", response.status);
              scope.setExtra("response_data", response.data);
          }
          // 排除 401 (通常是 token 过期，属于正常业务流程)
          // 排除 403 (通常是权限不足)
          // 可以根据需要决定是否上报
          if (response?.status !== 401) {
             Sentry.captureException(error);
          }
      });

      const errorMessageMode = config?.requestOptions?.errorMessageMode || 'none';
      
      const msg: string = response?.data?.message || '';
			const err: string = error?.toString?.() ?? '';
			let errMessage = '';

			try {
				if (error.code === 'ECONNABORTED' && error.message.includes('timeout')) {
					errMessage = '接口请求超时，请刷新页面重试!';
				}
				if (err?.includes('Network Error')) {
					errMessage = '网络异常，请检查您的网络连接是否正常!';
				}

				if (errMessage) {
					if (errorMessageMode === 'modal') {
						Modal.error({ title: '错误提示', content: errMessage });
					} else if (errorMessageMode === 'message') {
						message.error(errMessage);
					}
					return Promise.reject(error);
				}
			} catch (error) {
				throw new Error(error as unknown as string);
			}

			// 处理 401
			if (response?.status === 401) {
				// 如果是刷新 token 的请求本身 401 了，那只能跳登录页了
				if (config.url.includes('/refresh-token')) {
					clearAuth();
					window.location.href = '/login';
					return Promise.reject(error);
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
							resolve(defHttp.request(config));
						}, 1000); // 模拟耗时
					});
				} else {
					// 正在刷新，加入队列
					return new Promise((resolve) => {
						requestQueue.push((token) => {
							config.headers.Authorization = `Bearer ${token}`;
							resolve(defHttp.request(config));
						});
					});
				}
			}

			// 处理其他 status code
			const status = response?.status;
			switch (status) {
				case 403:
					errMessage = '用户得到授权，但是访问是被禁止的。!';
					break;
				case 404:
					errMessage = '网络请求错误,未找到该资源!';
					break;
				case 500:
					errMessage = '服务器错误,请联系管理员!';
					break;
				default:
					errMessage = msg || status;
			}

			if (errorMessageMode === 'modal') {
				Modal.error({ title: '错误提示', content: errMessage });
			} else if (errorMessageMode === 'message') {
				message.error(errMessage);
			}

			return Promise.reject(error);
		}
	},
	requestOptions: {
		// 默认将prefix 添加到url
		joinParamsToUrl: true,
		// 需要对返回数据进行处理
		isTransformResponse: true,
		// post请求的时候添加参数到url
		isReturnNativeResponse: false,
		// 默认接口前缀
		// prefixUrl: '/api', 
		// 消息提示类型
		errorMessageMode: 'message',
		// 默认忽略重复请求
		ignoreCancelToken: false,
		// 默认携带token
		withToken: true,
	},
};

export const defHttp = new VAxios(defaultOptions);
