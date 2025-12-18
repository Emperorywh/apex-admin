export interface Result<T = unknown> {
  code: number;
  message: string;
  data: T;
}

export interface RequestOptions {
  // Whether to join params to url
  joinParamsToUrl?: boolean;
  // Whether to format date
  formatDate?: boolean;
  // Whether to process request result
  isTransformResponse?: boolean;
  // Whether to return native response headers
  isReturnNativeResponse?: boolean;
  // Whether to ignore cancel token
  ignoreCancelToken?: boolean;
  // Whether to send token
  withToken?: boolean;
  // Error message mode
  errorMessageMode?: 'none' | 'modal' | 'message';
}

// Extend AxiosRequestConfig to include requestOptions
declare module 'axios' {
  export interface AxiosRequestConfig {
    requestOptions?: RequestOptions;
  }
}
