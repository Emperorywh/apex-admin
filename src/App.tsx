import { MenuFoldOutlined, MenuUnfoldOutlined, UploadOutlined, UserOutlined } from "@ant-design/icons";
import { Button, Layout, Menu, theme } from "antd";
import { useState } from "react";
import { Outlet, useMatches } from "react-router";
import type { AppRouteMeta } from "@/types/router";

const { Header, Sider, Content } = Layout;

function App() {

	const [collapsed, setCollapsed] = useState(false);

	// const navigate = useNavigate();

	const matches = useMatches();

	// 获取当前路由的 handle 数据
	const currentRouteMeta = matches.at(-1)?.handle as AppRouteMeta | undefined;

	console.log("matches", matches)

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
					// onClick={({ key, keyPath }) => {
					// 	console.log(key, keyPath);
					// 	navigate("/about");
					// }}
					items={[
						{
							key: '0',
							label: '首页',
							icon: <UserOutlined />,
						},
						{
							key: '1',
							label: '用户管理',
							icon: <UserOutlined />,
							children: [
								{
									key: '1-1',
									label: '个人资料',
								},
								{
									key: '1-2',
									label: '账户',
								},
							]
						},
						{
							key: '2',
							label: '系统管理',
							icon: <UserOutlined />,
							children: [
								{
									key: '2-1',
									label: '权限',
								},
								{
									key: '2-2',
									label: '角色',
								}
							]
						},
						{
							key: '3',
							icon: <UploadOutlined />,
							label: '多级菜单',
							children: [
								{
									key: '3-1',
									label: '多级菜单3-1',
								},
								{
									key: '3-2',
									label: '多级菜单3-2',
									children: [
										{
											key: '3-2-1',
											label: '多级菜单3-2-1',
										},
										{
											key: '3-2-2',
											label: '多级菜单3-2-2',
											children: [
												{
													key: '3-2-2-1',
													label: '多级菜单3-2-2-1',
												},
												{
													key: '3-2-2-2',
													label: '多级菜单3-2-2-2',
												},
												{
													key: '3-2-2-3',
													label: '多级菜单3-2-2-3',
												},
											]
										},
										{
											key: '3-2-3',
											label: '多级菜单3-2-2',
										},
									]
								},
							]
						},
						{
							key: '4',
							icon: <UploadOutlined />,
							label: '异常页',
							children: [
								{
									key: '4-1',
									label: '403',
								},
								{
									key: '4-2',
									label: '404',
								},
								{
									key: '4-3',
									label: '500',
								},
							]
						}
					]}
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
					<Outlet />
				</Content>
			</Layout>
		</Layout>
	)
}

export default App
