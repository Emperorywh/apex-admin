import { useNavigate, type NavigateOptions, type To } from "react-router";
import { useCallback } from "react";

export interface AppNavigateOptions extends NavigateOptions {
    /**
     * 目标页面的自定义标题（用于多开页面时区分 Tab）
     * 会自动拼接到 URL query 中，key 为 `title`
     */
    title?: string;
    /**
     * URL 查询参数对象
     * 会自动序列化并拼接到 URL 后
     */
    query?: Record<string, string | number | boolean | undefined | null>;
}

/**
 * 全局路由跳转封装 Hook
 * 
 * @description
 * 统一处理路由跳转逻辑，特别是针对 KeepAlive 多开页面的场景，
 * 自动处理 query 参数序列化和 title 注入，避免手动拼接字符串。
 * 
 * @example
 * const { push } = useAppNavigate();
 * 
 * // 普通跳转
 * push('/dashboard');
 * 
 * // 带参数跳转（自动序列化） -> /user/profile?id=1
 * push('/user/profile', { query: { id: 1 } });
 * 
 * // 多开页面（指定标题） -> /user/profile?id=2&title=用户详情
 * push('/user/profile', { query: { id: 2 }, title: '用户详情' });
 */
export const useAppNavigate = () => {
    const navigate = useNavigate();

    const push = useCallback((to: string, options?: AppNavigateOptions) => {
        const { query, title, ...rest } = options || {};
        
        let path = to;
        const searchParams = new URLSearchParams();

        // 1. 处理 query 参数
        if (query) {
            Object.entries(query).forEach(([key, value]) => {
                if (value !== undefined && value !== null && value !== '') {
                    searchParams.append(key, String(value));
                }
            });
        }

        // 2. 处理 title 参数（注入到 query 中供 useKeepAlive 识别）
        if (title) {
            searchParams.append('title', title);
        }

        // 3. 拼接参数
        const queryString = searchParams.toString();
        if (queryString) {
            // 如果 path 原本就有 ?，则使用 & 连接
            path += (path.includes('?') ? '&' : '?') + queryString;
        }

        navigate(path, rest);
    }, [navigate]);

    const replace = useCallback((to: string, options?: AppNavigateOptions) => {
        push(to, { ...options, replace: true });
    }, [push]);

    const back = useCallback(() => {
        navigate(-1);
    }, [navigate]);

    return {
        push,
        replace,
        back,
        navigate // 暴露原始 navigate 以备不时之需
    };
};
