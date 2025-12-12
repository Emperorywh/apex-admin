import { Tabs } from "antd"
import type { KeepAliveTabsProps } from "./index.types";
import styles from "./index.module.css";

const KeepAliveTabs: React.FC<KeepAliveTabsProps> = (props) => {

    const { items = [], activeKey, onRemove, onChange } = props;

    return <Tabs
        className={styles.tabs}
        hideAdd
        type="editable-card"
        onChange={onChange}
        activeKey={activeKey}
        items={items}
        onEdit={(key, action) => {
            if (action === 'remove') {
                onRemove?.(key as string);
            }
        }}
    />
}

export default KeepAliveTabs;