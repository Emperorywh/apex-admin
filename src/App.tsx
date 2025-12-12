import { MenuFoldOutlined, MenuUnfoldOutlined } from "@ant-design/icons";
import { Button, Layout, Menu, Spin, theme } from "antd";
import { Suspense, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useLocation, useNavigate, useOutlet } from "react-router";
const { Header, Sider, Content } = Layout;
import { SentryErrorBoundary } from "@/components/ErrorBoundary";
import { flatMenuItems, menuItems } from "./routes/utils";
import KeepAliveTabs from "./components/KeepAliveTabs";

function App() {

	const [collapsed, setCollapsed] = useState(false);

	const navigate = useNavigate();

	const loaction = useLocation();

	const uniqueId = loaction.pathname;

	const {
		token: { colorBgContainer, borderRadiusLG },
	} = theme.useToken();

	const [cachedNodes, setCachedNodes] = useState<Map<string, ReactNode>>(new Map());

	const currentElement = useOutlet();

	const currentElementRef = useRef<React.ReactNode>(null);

	const flatMenusMap = useMemo(() => {
		const map = new Map<string, { key: string, label: string }>();
		flatMenuItems.forEach(item => {
			map.set(item.key, item);
		});
		return map;
	}, [flatMenuItems]);

	const [tabItems, setTabItems] = useState<{ key: string, label: string }[]>([]);

	const [activeKey, setActiveKey] = useState('');

	const keepAliveNavigate = (key: string) => {
		navigate(key);
		setActiveKey(key);
	}

	useEffect(() => {
		const items: { key: string, label: string }[] = [];
		Array.from(cachedNodes.entries()).forEach(([id]) => {
			const menuItem = flatMenusMap.get(id);
			if (menuItem) {
				items.push({
					key: id,
					label: menuItem.label,
				});
			}
		});
		setTabItems(items);
	}, [cachedNodes])

	useEffect(() => {
		setActiveKey(loaction.pathname);
		keepAliveNavigate(loaction.pathname);
	}, []);

	useEffect(() => {
		// 如果当前有组件，且缓存中没有这个路径，则添加缓存
		if (currentElement) {
			setCachedNodes(prev => {
				// 如果已经存在，就不更新了，避免死循环
				if (prev.has(loaction.pathname)) {
					return prev;
				}
				const newMap = new Map(prev);
				// 将当前路径对应的组件存入缓存
				newMap.set(loaction.pathname, currentElement);
				return newMap;
			});
		}
	}, [currentElement, loaction.pathname]);

	return (
		<Layout className="w-screen h-screen">
			<Sider trigger={null} collapsible collapsed={collapsed} theme="light">
				<Menu
					theme="light"
					mode="inline"
					selectedKeys={[activeKey]}
					onClick={({ key }) => {
						keepAliveNavigate(key);
						setActiveKey(key);
					}}
					items={menuItems}
				/>
			</Sider>
			<Layout>
				<Header style={{ padding: 0, background: colorBgContainer, display: 'flex', alignItems: 'center' }}>
					<Button
						type="text"
						icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
						onClick={() => setCollapsed(!collapsed)}
						style={{
							fontSize: '16px',
							width: 64,
							height: 64,
						}}
					/>
					<KeepAliveTabs
						items={tabItems}
						onRemove={key => {
							if (tabItems.length <= 1) {
								return;
							}
							const isActive = key === activeKey;
							const findTabIndex = tabItems.findIndex(item => item.key === key);
							if (isActive && findTabIndex > -1) {
								const newActiveKey = tabItems[findTabIndex + 1]?.key || tabItems[findTabIndex - 1]?.key || uniqueId;
								keepAliveNavigate(newActiveKey);
								setActiveKey(newActiveKey);
							}
							setCachedNodes(prev => {
								const newMap = new Map(prev);
								newMap.delete(key);
								return newMap;
							});
						}}
						onChange={key => {
							keepAliveNavigate(key);
							setActiveKey(key);
						}}
						activeKey={activeKey}
					/>
				</Header>
				<Content
					style={{
						margin: '16px',
						padding: 24,
						minHeight: 280,
						background: colorBgContainer,
						borderRadius: borderRadiusLG,
					}}
				>
					<SentryErrorBoundary>
						<Suspense fallback={<Spin />}>
							{
								Array.from(cachedNodes.entries()).map(([id, element]) => (
									<div
										key={id}
										style={{ display: id === uniqueId ? 'block' : 'none' }}
									>
										{element}
									</div>
								))
							}
						</Suspense>
					</SentryErrorBoundary>
				</Content>
			</Layout>
		</Layout>
	)
}

export default App
