import { routeChildren } from "./config";
import type { AppRouteObject } from "@/types/router";
import type { MenuProps } from "antd";
import * as Icons from "@ant-design/icons";
import React from "react";

type MenuItem = Required<MenuProps>['items'][number];

/**
 * 动态渲染 Icon 组件
 */
const renderIcon = (icon?: string) => {
    if (!icon) return undefined;
    const IconComponent = (Icons as any)[icon];
    if (IconComponent) {
        return React.createElement(IconComponent);
    }
    return undefined;
};

/**
 * 递归生成菜单项
 * @param routes 路由数组
 * @param parentPath 父级路径
 */
const generateMenuItems = (routes: AppRouteObject[], parentPath = ""): MenuItem[] => {
    const items: MenuItem[] = [];

    routes.forEach((route) => {
        // 过滤掉没有 meta 或者没有 title 的路由
        // 同时过滤掉 index 路由（通常作为默认展示页，不显示在菜单）
        if (!route.handle?.title || route.index) {
            return;
        }

        // 处理路径拼接
        let currentPath = route.path;
        if (!currentPath?.startsWith('/') && !currentPath?.startsWith('http')) {
            // 如果 parentPath 结尾没有 / 且 currentPath 开头没有 /，加一个 /
            // 如果 parentPath 是 /，则不需要加 /
            const prefix = parentPath === '/' ? '' : parentPath + '/';
            currentPath = prefix + (route.path || '');
        }

        let childrenItems: MenuItem[] | undefined;
        if (route.children) {
            const items = generateMenuItems(route.children, currentPath);
            if (items.length > 0) {
                childrenItems = items;
            }
        }

        const item = {
            key: currentPath || '',
            label: route.handle.title,
            icon: renderIcon(route.handle.icon),
            children: childrenItems,
        } as MenuItem;

        items.push(item);
    });

    return items;
};

// 生成菜单数据
export const menuItems = generateMenuItems(routeChildren);
