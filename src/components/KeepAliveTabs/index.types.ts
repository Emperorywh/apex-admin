import type { BaseTabsProps } from "antd/es/tabs";

export interface KeepAliveTabsProps {
    activeKey: string;
    items: BaseTabsProps['items'];
    onRemove: (key: string) => void;
    onChange: (key: string) => void;
    setItems: React.Dispatch<React.SetStateAction<KeepAliveTabsItem[]>>;
    onRefresh?: (key: string) => void;
    onCloseOthers?: (key: string) => void;
    onCloseAll?: () => void;
}

export interface KeepAliveTabsItem {
    key: string;
    label: string;
}