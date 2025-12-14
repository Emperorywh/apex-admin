import { BellFilled, GithubFilled, MenuFoldOutlined, MenuUnfoldOutlined, SettingFilled } from "@ant-design/icons";
import { Avatar, Badge, Button, Divider, Dropdown, Layout, Menu, Popover, theme, Spin } from "antd";
import { useState, Suspense } from "react";
import { useNavigate } from "react-router";
const { Header, Sider, Content } = Layout;
import { menuItems } from "@/routes/utils";
import ReactLogo from "@/assets/react.svg?react";
import CnLogo from "@/assets/svg/cn.svg?react";
import GbLogo from "@/assets/svg/gb.svg?react";
import { useKeepAlive } from "./hooks/useKeepAlive";
import KeepAliveTabs from "@/components/KeepAliveTabs";
import { SentryErrorBoundary } from "@/components/ErrorBoundary";

function App() {

	const [collapsed, setCollapsed] = useState(false);

	const navigate = useNavigate();

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
