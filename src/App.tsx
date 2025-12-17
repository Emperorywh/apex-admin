import { GithubFilled, MenuFoldOutlined, MenuUnfoldOutlined } from "@ant-design/icons";
import { Button, Dropdown, Layout, Menu, theme, Spin } from "antd";
import { useState, Suspense, useMemo } from "react";
import { useNavigate } from "react-router";
const { Header, Sider, Content } = Layout;
import { generateMenuItems } from "@/routes/utils";
import { routeChildren } from "@/routes/config";
import ReactLogo from "@/assets/react.svg?react";
import CnLogo from "@/assets/svg/cn.svg?react";
import GbLogo from "@/assets/svg/gb.svg?react";
import { useKeepAlive } from "./hooks/useKeepAlive";
import KeepAliveTabs from "@/components/KeepAliveTabs";
import NotificationDrawer from "@/components/NotificationDrawer";
import { SentryErrorBoundary } from "@/components/ErrorBoundary";
import "@/i18n/i18n";
import { useTranslation } from "react-i18next";
import SettingsDrawer from "./components/SettingsDrawer";
import UserAvatar from "./components/UserAvatar";
import { useTheme } from "@/context/ThemeContext";

function App() {

	const { themeMode } = useTheme();
	const [collapsed, setCollapsed] = useState(false);

	const navigate = useNavigate();

	const { i18n, t } = useTranslation();

	const menuItems = useMemo(() => generateMenuItems(routeChildren, "", t), [i18n.language, t]);

	const [language, setLanguage] = useState<"zh-CN" | "en-US">("zh-CN");

	const {
		token: { colorBgContainer, borderRadiusLG },
	} = theme.useToken();

	const {
		activeKey,
		tabItems,
		setTabItems,
		cachedNodes,
		refreshKeys,
		onRemove,
		onChange,
		onCloseAll,
		onCloseOthers,
		onRefresh,
		uniqueId
	} = useKeepAlive();

	return (
		<Layout className="w-full h-full overflow-hidden">
			<Sider
				trigger={null}
				collapsible
				collapsed={collapsed}
				theme={themeMode}
				width={220}
				collapsedWidth={80}
				className="flex-none border-r border-gray-100 dark:border-gray-800"
			>
					<div className="flex items-center justify-center h-[64px] cursor-pointer" onClick={() => navigate('/dashboard')}>
						<ReactLogo className="w-[32px] h-[32px] animate-spin-slow text-[#00d8ff]" />
						<span className={`font-bold text-[18px] whitespace-nowrap overflow-hidden transition-all duration-300 ease-in-out ${collapsed ? 'max-w-0 opacity-0 ml-0' : 'max-w-[200px] opacity-100 ml-[10px]'} ${themeMode === 'dark' ? 'text-white' : 'text-black'}`}>Apex Admin</span>
					</div>
					<Menu
						theme={themeMode}
						mode="inline"
						selectedKeys={[activeKey]}
						onClick={({ key }) => {
							navigate(key);
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
									onRemove={onRemove}
									onChange={onChange}
									activeKey={activeKey}
									onCloseAll={onCloseAll}
									onCloseOthers={onCloseOthers}
									onRefresh={onRefresh}
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
											key: 'zh-CN',
										},
										{
											label: (
												<div className="flex items-center gap-2">
													<GbLogo className="w-5 h-5 rounded-sm" />
													<span>English</span>
												</div>
											),
											key: 'en-US',
										},
									],
									onClick: ({ key }) => {
										setLanguage(key as "zh-CN" | "en-US");
										i18n.changeLanguage(key);
									}
								}}
								placement="bottom"
								arrow
								trigger={['click']}

							>
								<div className="flex items-center cursor-pointer hover:bg-gray-100 p-1 rounded-sm transition-colors">
									{
										language === "zh-CN" && <CnLogo className="w-5 h-5 rounded-sm" />
									}
									{
										language === "en-US" && <GbLogo className="w-5 h-5 rounded-sm" />
									}
								</div>
							</Dropdown>
							<div
								className="group w-[35px] h-[35px] flex items-center justify-center cursor-pointer hover:bg-gray-100 p-1 rounded-full transition-colors"
								onClick={() => {
									window.open("https://github.com/Emperorywh/apex-admin", "_blank");
								}}
							>
								<GithubFilled className="font-bold text-[20px] group-hover:!text-gray-400 transition-colors" />
							</div>
							<NotificationDrawer />
							<SettingsDrawer />
							<UserAvatar />
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
											key={`${id}-${refreshKeys.get(id) || 0}`}
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
