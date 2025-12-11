import Dashboard from "@/pages/dashboard";
import UserProfile from "@/pages/user/profile";
import UserAccount from "@/pages/user/account";
import SystemPermission from "@/pages/system/permission";
import SystemRole from "@/pages/system/role";
import MultiLevel31 from "@/pages/multi/level-3-1";
import MultiLevel321 from "@/pages/multi/level-3-2/level-3-2-1";
import MultiLevel3221 from "@/pages/multi/level-3-2/level-3-2-2/level-3-2-2-1";
import MultiLevel3222 from "@/pages/multi/level-3-2/level-3-2-2/level-3-2-2-2";
import MultiLevel3223 from "@/pages/multi/level-3-2/level-3-2-2/level-3-2-2-3";
import MultiLevel323 from "@/pages/multi/level-3-2/level-3-2-3";
import Exception403 from "@/pages/exception/403";
import Exception404 from "@/pages/exception/404";
import Exception500 from "@/pages/exception/500";
import type { AppRouteObject } from "@/types/router";

export const routeChildren: AppRouteObject[] = [
    { index: true, Component: Dashboard },
    {
        path: 'dashboard',
        Component: Dashboard,
        handle: {
            title: "首页",
            icon: "UserOutlined"
        }
    },
    {
        path: 'user',
        handle: {
            title: "用户管理",
            icon: "UserOutlined"
        },
        children: [
            {
                path: 'profile',
                Component: UserProfile,
                handle: {
                    title: "个人资料",
                }
            },
            {
                path: 'account',
                Component: UserAccount,
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
            icon: "UserOutlined"
        },
        children: [
            {
                path: 'permission',
                Component: SystemPermission,
                handle: {
                    title: "权限",
                }
            },
            {
                path: 'role',
                Component: SystemRole,
                handle: {
                    title: "角色",
                }
            }
        ]
    },
    {
        path: 'multi',
        handle: {
            title: "多级菜单",
            icon: "UploadOutlined"
        },
        children: [
            {
                path: 'level-3-1',
                Component: MultiLevel31,
                handle: {
                    title: "多级菜单3-1",
                }
            },
            {
                path: 'level-3-2',
                handle: {
                    title: "多级菜单3-2",
                },
                children: [
                    {
                        path: 'level-3-2-1',
                        Component: MultiLevel321,
                        handle: {
                            title: "多级菜单3-2-1",
                        }
                    },
                    {
                        path: 'level-3-2-2',
                        handle: {
                            title: "多级菜单3-2-2",
                        },
                        children: [
                            {
                                path: 'level-3-2-2-1',
                                Component: MultiLevel3221,
                                handle: {
                                    title: "多级菜单3-2-2-1",
                                }
                            },
                            {
                                path: 'level-3-2-2-2',
                                Component: MultiLevel3222,
                                handle: {
                                    title: "多级菜单3-2-2-2",
                                }
                            },
                            {
                                path: 'level-3-2-2-3',
                                Component: MultiLevel3223,
                                handle: {
                                    title: "多级菜单3-2-2-3",
                                }
                            }
                        ]
                    },
                    {
                        path: 'level-3-2-3',
                        Component: MultiLevel323,
                        handle: {
                            title: "多级菜单3-2-3",
                        }
                    }
                ]
            }
        ]
    },
    {
        path: 'exception',
        handle: {
            title: "异常页",
            icon: "UploadOutlined"
        },
        children: [
            {
                path: '403',
                Component: Exception403,
                handle: {
                    title: "403",
                }
            },
            {
                path: '404',
                Component: Exception404,
                handle: {
                    title: "404",
                }
            },
            {
                path: '500',
                Component: Exception500,
                handle: {
                    title: "500",
                }
            }
        ]
    }
];
