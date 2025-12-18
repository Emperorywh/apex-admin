import { useEffect, useState, type ReactNode, useCallback, useMemo } from "react";
import { useLocation, useOutlet } from "react-router";
import { generateMenuItems, generateFlatMenus } from "@/routes/utils";
import { routeChildren } from "@/routes/config";
import { useAppNavigate } from "./useAppNavigate";
import { useTranslation } from "react-i18next";

export interface KeepAliveTab {
    key: string;
    label: string;
}

export const useKeepAlive = () => {
    const { t } = useTranslation();
    const { push } = useAppNavigate();
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

    // 使用 useMemo 动态生成 flatMenusMap，依赖于 t 函数
    const flatMenusMap = useMemo(() => {
        const menus = generateMenuItems(routeChildren, "", t);
        const flatMenus = generateFlatMenus(menus);
        const map = new Map<string, string>();
        flatMenus.forEach((item: { key: string, label: string }) => map.set(item.key, item.label));
        return map;
    }, [t]);

    /**
     * 监听路由变化，更新缓存 和 Tab 列表
     * 将两个 effect 合并，避免级联更新
     */
    useEffect(() => {
        if (!currentElement) return;

        // 1. 更新缓存
        // eslint-disable-next-line react-hooks/set-state-in-effect
        setCachedNodes((prev) => {
            if (prev.has(uniqueId)) {
                return prev;
            }
            const newMap = new Map(prev);
            newMap.set(uniqueId, currentElement);
            return newMap;
        });

        // 2. 更新 Tab 列表 (直接基于 uniqueId 判断，而不是等待 cachedNodes 更新)
        setTabItems((prevTabs) => {
            // 如果 tab 已存在，不处理
            if (prevTabs.some(tab => tab.key === uniqueId)) {
                return prevTabs;
            }

            const [path] = uniqueId.split('?');
            let label = flatMenusMap.get(path);

            // 只添加在菜单中定义过的页面
            if (label) {
                const searchParams = new URLSearchParams(uniqueId.split('?')[1]);
                const queryTitle = searchParams.get('title');
                if (queryTitle) {
                    label = queryTitle;
                }
                return [...prevTabs, { key: uniqueId, label }];
            }
            return prevTabs;
        });

    }, [currentElement, uniqueId, flatMenusMap]);

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
                    push(nextTab.key);
                }
            }
        }

        // 更新状态
        setCachedNodes((prev) => {
            const newMap = new Map(prev);
            newMap.delete(key);
            return newMap;
        });
        
        setTabItems(prev => prev.filter(item => item.key !== key));

        // 清理 refreshKey
        setRefreshKeys(prev => {
            if (prev.has(key)) {
                const newMap = new Map(prev);
                newMap.delete(key);
                return newMap;
            }
            return prev;
        });
    }, [tabItems, uniqueId, push]);

    /**
     * 切换 Tab
     */
    const onChange = useCallback((key: string) => {
        push(key);
    }, [push]);

    /**
     * 关闭所有
     */
    const onCloseAll = useCallback(() => {
        setCachedNodes(new Map());
        setRefreshKeys(new Map());
        setTabItems([]); // 会触发重新生成当前页面的 tab
        push("/dashboard");
    }, [push]);

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
        
        setTabItems(prev => prev.filter(item => item.key === key));
        
        if (uniqueId !== key) {
            push(key);
        }
    }, [uniqueId, push]);

    /**
     * 刷新当前 Tab
     */
    const onRefresh = useCallback((key: string) => {
        setRefreshKeys(prev => {
            const newMap = new Map(prev);
            newMap.set(key, (newMap.get(key) || 0) + 1);
            return newMap;
        });
    }, []);

    // 动态计算 labels
    const tabItemsWithLabels = useMemo(() => {
        return tabItems.map(tab => {
             const [path] = tab.key.split('?');
             // 优先使用 query 中的 title
             const searchParams = new URLSearchParams(tab.key.split('?')[1]);
             const queryTitle = searchParams.get('title');
             if (queryTitle) {
                 return { ...tab, label: queryTitle };
             }

             const newLabel = flatMenusMap.get(path);
             if (newLabel) {
                 return { ...tab, label: newLabel };
             }
             return tab;
        });
    }, [tabItems, flatMenusMap]);

    return {
        activeKey: uniqueId,
        tabItems: tabItemsWithLabels,
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
