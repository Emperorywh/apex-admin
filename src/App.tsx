import { MenuFoldOutlined, MenuUnfoldOutlined } from "@ant-design/icons";
import { Button, Layout, Menu, theme } from "antd";
import { useState } from "react";
import { Outlet, useMatches, useNavigate } from "react-router";
import type { AppRouteMeta } from "@/types/router";
const { Header, Sider, Content } = Layout;
import { SentryErrorBoundary } from "@/components/ErrorBoundary";
import { menuItems } from "./routes/utils";

function App() {

	const [collapsed, setCollapsed] = useState(false);

	const navigate = useNavigate();

	const matches = useMatches();

	// 获取当前路由的 handle 数据
	const currentRouteMeta = matches.at(-1)?.handle as AppRouteMeta | undefined;

	const {
		token: { colorBgContainer, borderRadiusLG },
	} = theme.useToken();


	return (
		<Layout className="w-screen h-screen">
			<Sider trigger={null} collapsible collapsed={collapsed} theme="light">
				<Menu
					theme="light"
					mode="inline"
					defaultSelectedKeys={['1']}
					onClick={({ key }) => {
						navigate(key);
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
					<span style={{ fontSize: '18px', fontWeight: 'bold' }}>
						{currentRouteMeta?.title}
					</span>
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
					    <Outlet />
                    </SentryErrorBoundary>
				</Content>
			</Layout>
		</Layout>
	)
}

export default App
