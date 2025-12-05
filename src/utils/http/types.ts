export interface Result<T = any> {
  code: number;
  message: string;
  data: T;
}

export interface RequestOptions {
  // 是否将请求参数拼接到 url
  joinParamsToUrl?: boolean;
  // 是否格式化请求参数时间
  formatDate?: boolean;
  // 是否处理请求结果
  isTransformResponse?: boolean;
  // 是否返回原生响应头 比如：需要获取响应头时使用该属性
  isReturnNativeResponse?: boolean;
  // 是否忽略重复请求
  ignoreCancelToken?: boolean;
  // 是否携带 token
  withToken?: boolean;
  // 错误消息提示类型
  errorMessageMode?: 'none' | 'modal' | 'message' | undefined;
}

import type { AxiosRequestConfig, AxiosResponse } from 'axios';

export interface CreateAxiosOptions extends AxiosRequestConfig {
  authenticationScheme?: string;
  transform?: AxiosTransform;
  requestOptions?: RequestOptions;
}

export abstract class AxiosTransform {
  /**
   * @description: 处理请求数据。如果数据不是预期格式，可直接抛出错误
   */
  transformRequestHook?: (res: AxiosResponse<Result>, options: RequestOptions) => any;

  /**
   * @description: 请求失败处理
   */
  requestCatchHook?: (e: Error, options: RequestOptions) => Promise<any>;

  /**
   * @description: 请求之前的拦截器
   */
  beforeRequestHook?: (config: AxiosRequestConfig, options: RequestOptions) => AxiosRequestConfig;

  /**
   * @description: 请求拦截器处理
   */
  requestInterceptors?: (config: AxiosRequestConfig, options: CreateAxiosOptions) => AxiosRequestConfig;

  /**
   * @description: 请求拦截器错误处理
   */
  requestInterceptorsCatch?: (error: any) => Promise<any>;

  /**
   * @description: 响应拦截器处理
   */
  responseInterceptors?: (res: AxiosResponse<any>) => AxiosResponse<any>;

  /**
   * @description: 响应错误处理
   */
  responseInterceptorsCatch?: (error: any) => Promise<any>;
}
