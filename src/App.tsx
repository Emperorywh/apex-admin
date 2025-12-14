import { BellFilled, GithubFilled, MenuFoldOutlined, MenuUnfoldOutlined, SettingFilled } from "@ant-design/icons";
import { Avatar, Badge, Button, Divider, Dropdown, Layout, Menu, Popover, Spin, theme } from "antd";
import { Suspense, useEffect, useMemo, useState, type ReactNode } from "react";
import { useLocation, useNavigate, useOutlet } from "react-router";
const { Header, Sider, Content } = Layout;
import { SentryErrorBoundary } from "@/components/ErrorBoundary";
import { flatMenuItems, menuItems } from "@/routes/utils";
import KeepAliveTabs from "@/components/KeepAliveTabs";
import ReactLogo from "@/assets/react.svg?react";
import CnLogo from "@/assets/svg/cn.svg?react";
import GbLogo from "@/assets/svg/gb.svg?react";

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
		<Layout className="w-full h-full overflow-hidden">
			<Sider
				trigger={null}
				collapsible
				collapsed={collapsed}
				theme="light"
				width={220}
				collapsedWidth={80}
				className="flex-none border-r border-gray-100"
			>
				<div className="flex items-center justify-center h-[64px] cursor-pointer" onClick={() => navigate('/dashboard')}>
					<ReactLogo className="w-[32px] h-[32px] animate-spin-slow text-[#00d8ff]" />
					<span className={`font-bold text-[18px] whitespace-nowrap overflow-hidden transition-all duration-300 ease-in-out ${collapsed ? 'max-w-0 opacity-0 ml-0' : 'max-w-[200px] opacity-100 ml-[10px]'}`}>Apex Admin</span>
				</div>
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
				<Header className="flex items-center overflow-hidden" style={{ padding: 0, background: colorBgContainer }}>
					<div className="flex-1 flex items-center gap-[5px] min-w-0">
						<Button
							type="text"
							icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
							onClick={() => setCollapsed(!collapsed)}
							style={{
								fontSize: '16px',
								width: 64,
								height: 64,
							}}
							className="shrink-0"
						/>
						<div className="flex-1 overflow-hidden min-w-0">
							<KeepAliveTabs
								items={tabItems}
								setItems={setTabItems}
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
						</div>
					</div>
					<div className="flex-none flex items-center gap-[10px] px-[10px]">
						<Dropdown
							menu={{
								items: [
									{
										label: (
											<div className="flex items-center gap-2">
												<CnLogo className="w-5 h-5 rounded-sm" />
												<span>中文</span>
											</div>
										),
										key: '1',
									},
									{
										label: (
											<div className="flex items-center gap-2">
												<GbLogo className="w-5 h-5 rounded-sm" />
												<span>English</span>
											</div>
										),
										key: '2',
									},
								]
							}}
							placement="bottom"
							arrow
							trigger={['click']}
						>
							<div className="flex items-center cursor-pointer hover:bg-gray-100 p-1 rounded-sm transition-colors">
								<CnLogo className="w-5 h-5 rounded-sm" />
							</div>
						</Dropdown>
						<div className="group w-[35px] h-[35px] flex items-center justify-center cursor-pointer hover:bg-gray-100 p-1 rounded-full transition-colors">
							<GithubFilled className="font-bold text-[20px] group-hover:!text-gray-400 transition-colors" />
						</div>
						<Badge count={5} size="small">
							<div className="cursor-pointer hover:bg-gray-100 p-1 rounded-sm transition-colors">
								<BellFilled className="font-bold text-[20px]" />
							</div>
						</Badge>
						<div className="group w-[35px] h-[35px] flex items-center justify-center cursor-pointer hover:bg-gray-100 p-1 rounded-full transition-colors">
							<SettingFilled className="font-bold text-[20px] animate-spin-slow group-hover:!text-gray-400 transition-colors" />
						</div>
						<Popover
							content={
								<div className="">
									<Divider size="small" />
									<div className="cursor-pointer hover:bg-gray-100 px-2 py-1 rounded-sm transition-colors">
										<span>个人资料</span>
									</div>
									<div className="cursor-pointer hover:bg-gray-100 px-2 py-1 rounded-sm transition-colors">
										<span>账户</span>
									</div>
									<Divider size="small" />
									<div className="cursor-pointer hover:bg-gray-100 px-2 py-1 rounded-sm transition-colors text-[#ff4d4f] hover:text-[#232222]">
										<span>退出</span>
									</div>
								</div>
							}
							title={
								<div className="flex items-center gap-5">
									<Avatar size={"small"} src="https://gw.alipayobjects.com/zos/rmsportal/KDpgvguMpGfqaHPjicRK.svg" />
									<div className="flex flex-col text-[14px]">
										<span className="font-bold">admin</span>
										<span className="text-[12px] text-[#666]">imyangwenhua@gmail.com</span>
									</div>
								</div>
							}
							trigger="click"
						>
							<Avatar className="cursor-pointer" size={"small"} src="https://gw.alipayobjects.com/zos/rmsportal/KDpgvguMpGfqaHPjicRK.svg" />
						</Popover>
					</div>
				</Header>
				<Content
					style={{
						margin: '10px',
						padding: 10,
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
