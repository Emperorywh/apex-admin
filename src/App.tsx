import { GithubFilled, MenuFoldOutlined, MenuUnfoldOutlined } from "@ant-design/icons";
import { Button, Layout, Menu, theme, Spin } from "antd";
import { useState, Suspense, useMemo } from "react";
const { Header, Sider, Content } = Layout;
import { generateMenuItems } from "@/routes/utils";
import { routeChildren } from "@/routes/config";
import ReactLogo from "@/assets/react.svg?react";
import { useKeepAlive } from "./hooks/useKeepAlive";
import KeepAliveTabs from "@/components/KeepAliveTabs";
import NotificationDrawer from "@/components/NotificationDrawer";
import { SentryErrorBoundary } from "@/components/ErrorBoundary";
import "@/i18n/i18n";
import { useTranslation } from "react-i18next";
import SettingsDrawer from "./components/SettingsDrawer";
import UserAvatar from "./components/UserAvatar";
import { useTheme } from "@/hooks/useTheme";
import LanguageSelector from "@/components/LanguageSelector";
import { useAppNavigate } from "./hooks/useAppNavigate";

function App() {

	const { themeMode, layoutMode } = useTheme();
	const [collapsed, setCollapsed] = useState(false);

	const { push } = useAppNavigate();

	const { t } = useTranslation();

	const menuItems = useMemo(() => generateMenuItems(routeChildren, "", t), [t]);

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

	const headerTools = (
		<div className="flex-none flex items-center gap-[10px] px-[10px]">
			<LanguageSelector />
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
	);

	const contentArea = (
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
	);

	if (layoutMode === 'top') {
		return (
			<Layout className="w-full h-full overflow-hidden">
				<Header
					className="flex items-center justify-between px-4 border-b border-gray-100 dark:border-gray-800"
					style={{ background: colorBgContainer, paddingInline: 16 }}
				>
					<div className="flex items-center cursor-pointer" onClick={() => push('/dashboard')}>
						<ReactLogo className="w-[32px] h-[32px] animate-spin-slow text-[#00d8ff] mr-2" />
						<span className={`font-bold text-[18px] ${themeMode === 'dark' ? 'text-white' : 'text-black'}`}>Apex Admin</span>
					</div>
					<div className="flex-1 min-w-0 mx-8">
						<Menu
							theme={themeMode}
							mode="horizontal"
							selectedKeys={[activeKey]}
							onClick={({ key }) => push(key)}
							items={menuItems}
							style={{ borderBottom: 'none', lineHeight: '64px', background: 'transparent' }}
						/>
					</div>
					{headerTools}
				</Header>
				<div className="px-4 pt-2">
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
				<Content
					style={{
						margin: '10px',
						padding: 10,
						minHeight: 280,
						background: colorBgContainer,
						borderRadius: borderRadiusLG,
						overflow: 'auto'
					}}
				>
					{contentArea}
				</Content>
			</Layout>
		);
	}

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
				<div className="flex items-center justify-center h-[64px] cursor-pointer" onClick={() => push('/dashboard')}>
					<ReactLogo className="w-[32px] h-[32px] animate-spin-slow text-[#00d8ff]" />
					<span className={`font-bold text-[18px] whitespace-nowrap overflow-hidden transition-all duration-300 ease-in-out ${collapsed ? 'max-w-0 opacity-0 ml-0' : 'max-w-[200px] opacity-100 ml-[10px]'} ${themeMode === 'dark' ? 'text-white' : 'text-black'}`}>Apex Admin</span>
				</div>
				<Menu
					theme={themeMode}
					mode="inline"
					selectedKeys={[activeKey]}
					onClick={({ key }) => {
						push(key);
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
					{headerTools}
				</Header>
				<Content
					style={{
						margin: '10px',
						padding: 10,
						minHeight: 280,
						background: colorBgContainer,
						borderRadius: borderRadiusLG,
						overflow: 'auto'
					}}
				>
					{contentArea}
				</Content>
			</Layout>
		</Layout>
	)
}

export default App
