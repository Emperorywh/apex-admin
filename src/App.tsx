import { MenuFoldOutlined, MenuUnfoldOutlined } from "@ant-design/icons";
import { Button, Layout, Menu, Spin, theme } from "antd";
import { Suspense, useState } from "react";
import { useNavigate } from "react-router";
const { Header, Sider, Content } = Layout;
import { SentryErrorBoundary } from "@/components/ErrorBoundary";
import { menuItems } from "./routes/utils";
import KeepAlive from "./components/KeepAlive";
import KeepAliveTabs from "./components/KeepAliveTabs";

function App() {

	const [collapsed, setCollapsed] = useState(false);

	const navigate = useNavigate();

	const {
		token: { colorBgContainer, borderRadiusLG },
	} = theme.useToken();

	return (
		<Layout className="w-screen h-screen">
			<Sider trigger={null} collapsible collapsed={collapsed} theme="light">
				<Menu
					theme="light"
					mode="inline"
					defaultSelectedKeys={['/dashboard']}
					onClick={({ key }) => {
						navigate(`${key}`, {
							state: {
								label: ''
							}
						});
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
					<KeepAliveTabs />
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
							<KeepAlive />
						</Suspense>
					</SentryErrorBoundary>
				</Content>
			</Layout>
		</Layout>
	)
}

export default App
