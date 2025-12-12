import type { BaseTabsProps } from "antd/es/tabs";

export interface KeepAliveTabsProps {
    activeKey: string;
    items: BaseTabsProps['items'];
    onRemove: (key: string) => void;
    onChange: (key: string) => void;
}

export interface KeepAliveTabsItem {
    key: string;
    label: string;
}