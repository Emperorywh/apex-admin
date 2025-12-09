
import React from 'react';
import { Button, Result } from 'antd';
import * as Sentry from '@sentry/react';

interface FallbackProps {
    error: Error;
    resetErrorBoundary: () => void;
}

const ErrorFallback: React.FC<FallbackProps> = ({ error, resetErrorBoundary }) => {
    return (
        <Result
            status="500"
            title="出错了"
            subTitle={`抱歉，发生了一些意外错误：${error.message}`}
            extra={[
                <Button type="primary" key="reload" onClick={resetErrorBoundary}>
                    重试
                </Button>,
                <Button key="home" onClick={() => window.location.href = '/'}>
                    返回首页
                </Button>
            ]}
        />
    );
};

// 使用 Sentry 提供的 HOC 包装 ErrorBoundary
// 这将自动捕获 React 组件树中的错误并上报给 Sentry
export const SentryErrorBoundary = Sentry.withErrorBoundary(
    ({ children }: { children: React.ReactNode }) => <>{children}</>,
    {
        fallback: ({ error, resetError }: { error: unknown; resetError: () => void }) => (
            <ErrorFallback 
                error={error instanceof Error ? error : new Error(String(error))} 
                resetErrorBoundary={resetError} 
            />
        ),
        showDialog: false, // 可以在这里开启 Sentry 的用户反馈弹窗
    }
);
