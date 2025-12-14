import { useEffect, useState, type ReactNode, useCallback, useRef } from "react";
import { useLocation, useNavigate, useOutlet } from "react-router";
import { flatMenuItems } from "@/routes/utils";

export interface KeepAliveTab {
    key: string;
    label: string;
}

export const useKeepAlive = () => {
    const navigate = useNavigate();
    const location = useLocation();
    const currentElement = useOutlet();
    // 使用 pathname + search 作为唯一标识，支持同一路由不同参数多开
    const uniqueId = location.pathname + location.search;

    // 缓存的组件节点
    const [cachedNodes, setCachedNodes] = useState<Map<string, ReactNode>>(new Map());
    // 标签页列表（支持拖拽排序，所以需要独立 State）
    const [tabItems, setTabItems] = useState<KeepAliveTab[]>([]);
    // 刷新 Key 用于强制重新渲染
    const [refreshKeys, setRefreshKeys] = useState<Map<string, number>>(new Map());

    // 使用 Ref 存储 flatMenusMap，避免每次渲染都重建 Map
    // 这里假设 flatMenuItems 是静态的，不会在运行时改变
    const flatMenusMap = useRef<Map<string, string>>(null);
    if (!flatMenusMap.current) {
        flatMenusMap.current = new Map();
        flatMenuItems.forEach((item: any) => {
            flatMenusMap.current?.set(item.key, item.label);
        });
    }

    /**
     * 监听路由变化，更新缓存
     */
    useEffect(() => {
        if (!currentElement) return;

        setCachedNodes((prev) => {
            // 如果已存在，直接返回 prev，避免不必要的重渲染
            if (prev.has(uniqueId)) {
                return prev;
            }
            const newMap = new Map(prev);
            newMap.set(uniqueId, currentElement);
            return newMap;
        });
    }, [currentElement, uniqueId]);

    /**
     * 同步 tabItems 和 cachedNodes
     * 保持 tabItems 的顺序，同时添加新 tab 或移除不存在的 tab
     */
    useEffect(() => {
        setTabItems((prevTabs) => {
            const newTabs = [...prevTabs];
            const cachedKeys = new Set(cachedNodes.keys());
            const currentTabKeys = new Set(prevTabs.map(t => t.key));

            // 1. 移除 cachedNodes 中不存在的 tab
            // 使用 filterRight 或从后往前遍历删除，或者直接 filter
            const filteredTabs = newTabs.filter(tab => cachedKeys.has(tab.key));

            // 2. 添加 cachedNodes 中有但 tabs 中没有的 tab
            let hasNewTab = false;
            for (const key of cachedNodes.keys()) {
                if (!currentTabKeys.has(key)) {
                    // 移除 query 参数来匹配菜单中的 path
                    const [path] = key.split('?');
                    let label = flatMenusMap.current?.get(path);
                    
                    // 只添加在菜单中定义过的页面
                    if (label) {
                        // 如果有 query 参数，可以考虑在 label 后添加标识，这里暂且保持原样或由业务决定
                        // 简单实现：如果是多开页面，尝试从 query 中获取 title 参数，或者直接显示原标题
                        const searchParams = new URLSearchParams(key.split('?')[1]);
                        const queryTitle = searchParams.get('title');
                        if (queryTitle) {
                            label = queryTitle;
                        }

                        filteredTabs.push({ key, label });
                        hasNewTab = true;
                    }
                }
            }

            // 如果没有变化，返回 prevTabs 保持引用稳定
            if (!hasNewTab && filteredTabs.length === prevTabs.length) {
                return prevTabs;
            }

            return filteredTabs;
        });
    }, [cachedNodes]);

    /**
     * 路由跳转封装
     */
    const keepAliveNavigate = useCallback((key: string) => {
        navigate(key);
    }, [navigate]);

    /**
     * 移除 Tab
     */
    const onRemove = useCallback((key: string) => {
        if (tabItems.length <= 1) {
            return;
        }

        // 计算下一个激活的 key
        if (key === uniqueId) {
            const findTabIndex = tabItems.findIndex(item => item.key === key);
            if (findTabIndex > -1) {
                // 尝试取后一个，如果没有则取前一个
                const nextTab = tabItems[findTabIndex + 1] || tabItems[findTabIndex - 1];
                if (nextTab) {
                    keepAliveNavigate(nextTab.key);
                }
            }
        }

        // 更新状态
        setCachedNodes((prev) => {
            const newMap = new Map(prev);
            newMap.delete(key);
            return newMap;
        });
        
        // 清理 refreshKey
        setRefreshKeys(prev => {
            if (prev.has(key)) {
                const newMap = new Map(prev);
                newMap.delete(key);
                return newMap;
            }
            return prev;
        });
    }, [tabItems, uniqueId, keepAliveNavigate]);

    /**
     * 切换 Tab
     */
    const onChange = useCallback((key: string) => {
        keepAliveNavigate(key);
    }, [keepAliveNavigate]);

    /**
     * 关闭所有
     */
    const onCloseAll = useCallback(() => {
        // 保留当前页或者跳转到首页？
        // 现有逻辑是跳转到 /dashboard 并清空
        // 我们可以优化为：如果有 dashboard，保留 dashboard；否则全清空并跳转 dashboard
        setCachedNodes(new Map());
        setRefreshKeys(new Map());
        keepAliveNavigate("/dashboard");
    }, [keepAliveNavigate]);

    /**
     * 关闭其他
     */
    const onCloseOthers = useCallback((key: string) => {
        setCachedNodes((prev) => {
            const newMap = new Map();
            const currentNode = prev.get(key);
            if (currentNode) {
                newMap.set(key, currentNode);
            }
            return newMap;
        });
        
        // 这里的 setTabItems 其实会被 useEffect 自动处理，但为了即时性也可以手动设置
        // 不过依靠 useEffect 同步是更单一数据源的做法
        
        if (uniqueId !== key) {
            keepAliveNavigate(key);
        }
    }, [uniqueId, keepAliveNavigate]);

    /**
     * 刷新 Tab
     */
    const onRefresh = useCallback((key: string) => {
        setRefreshKeys((prev) => {
            const newMap = new Map(prev);
            newMap.set(key, (newMap.get(key) || 0) + 1);
            return newMap;
        });
        if (uniqueId !== key) {
            keepAliveNavigate(key);
        }
    }, [uniqueId, keepAliveNavigate]);

    return {
        activeKey: uniqueId,
        tabItems,
        setTabItems,
        cachedNodes,
        refreshKeys,
        onRemove,
        onChange,
        onCloseAll,
        onCloseOthers,
        onRefresh,
        uniqueId
    };
};
