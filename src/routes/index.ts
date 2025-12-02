import { createBrowserRouter, type RouteObject } from "react-router";
import App from "@/App";
import Home from "@/pages/home";
import Login from "@/pages/login";
import type { AppRouteObject } from "@/types/router";
import Personal from "@/pages/user/personal";
import Account from "@/pages/user/account";
import Permission from "@/pages/system/permission";
import Role from "@/pages/system/role";
import Menu31 from "@/pages/multilevel-menu/menu-3-1";
import Menu321 from "@/pages/multilevel-menu/menu-3-2/menu-3-2-1";
import Menu323 from "@/pages/multilevel-menu/menu-3-2/menu-3-2-3";
import Menu322 from "@/pages/multilevel-menu/menu-3-2/menu-3-2-2";

const routes: AppRouteObject[] = [
    {
        path: '/',
        Component: App,
        handle: {
            title: "首页",
            icon: "HomeOutlined"
        },
        children: [
            { index: true, Component: Home },
            {
                path: 'home',
                Component: Home,
                handle: {
                    title: "首页",
                    icon: "HomeOutlined"
                }
            },
            {
                path: 'user',
                handle: {
                    title: "用户管理",
                    icon: "HomeOutlined"
                },
                children: [
                    {
                        path: 'personal',
                        Component: Personal,
                        handle: {
                            title: "个人资料",
                        }
                    },
                    {
                        path: 'account',
                        Component: Account,
                        handle: {
                            title: "账户",
                        }
                    }
                ]
            },
            {
                path: 'system',
                handle: {
                    title: "系统管理",
                    icon: "HomeOutlined"
                },
                children: [
                    {
                        path: 'permission',
                        Component: Permission,
                        handle: {
                            title: "权限",
                        }
                    },
                    {
                        path: 'role',
                        Component: Role,
                        handle: {
                            title: "角色",
                        }
                    }
                ]
            },
            {
                path: 'multilevel-menu',
                handle: {
                    title: "多级菜单",
                    icon: "HomeOutlined"
                },
                children: [
                    {
                        path: 'menu-3-1',
                        Component: Menu31,
                        handle: {
                            title: "menu-3-1",
                        }
                    },
                    {
                        path: 'menu-3-2',
                        handle: {
                            title: "menu-3-2",
                        },
                        children: [
                            {
                                path: 'menu-3-2-1',
                                Component: Menu321,
                                handle: {
                                    title: "menu-3-2-1",
                                }
                            },
                            {
                                path: 'menu-3-2-2',
                                Component: Menu322,
                                handle: {
                                    title: "menu-3-2-2",
                                }
                            },
                            {
                                path: 'menu-3-2-3',
                                Component: Menu323,
                                handle: {
                                    title: "menu-3-2-3",
                                }
                            },
                        ]
                    },
                ]
            },
        ]
    },
    {
        path: 'login',
        Component: Login,
        handle: { title: "登录" }
    }
];

export const router = createBrowserRouter(routes as RouteObject[]);