import { Tabs } from "antd"
import { useEffect, useState } from "react";

const KeepAliveTabs: React.FC = () => {

    const [activeKey, setActiveKey] = useState('');

    const [items, setItems] = useState([]);

    const onChange = (newActiveKey: string) => {
        setActiveKey(newActiveKey);
    };

    useEffect(() => {
        setItems([]);
    }, []);

    return <Tabs
        hideAdd
        type="editable-card"
        onChange={onChange}
        activeKey={activeKey}
        items={items}
    />
}

export default KeepAliveTabs;