import React, { useState } from 'react';
import { Drawer, Badge, Tabs, Button } from 'antd';
import { BellFilled, TeamOutlined, BellOutlined } from '@ant-design/icons';
import dayjs from 'dayjs';
import relativeTime from 'dayjs/plugin/relativeTime';
import 'dayjs/locale/zh-cn';
import type { NotificationItem } from './index.types';
import NotificationList from './NotificationList';

dayjs.extend(relativeTime);
dayjs.locale('zh-cn');

const mockNotifications: NotificationItem[] = [
	{
		id: '1',
		title: '你收到了 14 份新周报',
		description: '请尽快查阅并回复。',
		datetime: '2025-12-17 10:00:00',
		read: false,
		type: 'notification',
		group: 'all',
		avatar: 'https://gw.alipayobjects.com/zos/rmsportal/ThXAXghbEsBCCSDihZxY.png',
	},
	{
		id: '2',
		title: '朱偏右 回复了你',
		description: '这种模板用于提醒谁与你发生了互动，左侧放『谁』的头像',
		datetime: '2025-12-16 18:30:00',
		read: false,
		type: 'message',
		group: 'team',
		avatar: 'https://gw.alipayobjects.com/zos/rmsportal/OKJXWOLhpRaF9QcvCjLy.png',
	},
	{
		id: '3',
		title: '任务名称',
		description: '任务需要在 2025-12-20 前启动',
		datetime: '2025-12-15 09:00:00',
		read: true,
		type: 'event',
		group: 'all',
	},
	{
		id: '4',
		title: '第三方紧急通知',
		description: '系统将在今晚进行维护，请注意保存数据。',
		datetime: '2025-12-14 14:00:00',
		read: true,
		type: 'notification',
		group: 'all',
		avatar: 'https://gw.alipayobjects.com/zos/rmsportal/kISTdvpyTAhtGxpovNWd.png',
	},
	{
		id: '5',
		title: '团队会议通知',
		description: '下午3点在会议室A进行周会。',
		datetime: '2025-12-17 11:00:00',
		read: false,
		type: 'notification',
		group: 'team',
	}
];

const NotificationDrawer: React.FC = () => {
	const [visible, setVisible] = useState(false);
	const [notifications, setNotifications] = useState<NotificationItem[]>(mockNotifications);
	const [activeTab, setActiveTab] = useState<'all' | 'team'>('all');

	const showDrawer = () => {
		setVisible(true);
	};

	const onClose = () => {
		setVisible(false);
	};

	const markAsRead = (id: string) => {
		setNotifications((prev) =>
			prev.map((item) => (item.id === id ? { ...item, read: true } : item))
		);
	};

	const markAllAsRead = () => {
		setNotifications((prev) =>
			prev.map((item) => (item.group === activeTab || activeTab === 'all' ? { ...item, read: true } : item))
		);
	};

	const clearNotifications = () => {
		setNotifications((prev) =>
			prev.filter((item) => !(item.group === activeTab || activeTab === 'all'))
		);
	};

	const unreadCount = notifications.filter((item) => !item.read).length;

	const getFilteredNotifications = (group: 'all' | 'team') => {
		if (group === 'all') return notifications;
		return notifications.filter((item) => item.group === group);
	};

	const items = [
		{
			key: 'all',
			label: `全部 (${notifications.length})`,
			children: (
				<NotificationList
					data={getFilteredNotifications('all')}
					onMarkAsRead={markAsRead}
				/>
			),
			icon: <BellOutlined />,
		},
		{
			key: 'team',
			label: `团队 (${notifications.filter(n => n.group === 'team').length})`,
			children: (
				<NotificationList
					data={getFilteredNotifications('team')}
					onMarkAsRead={markAsRead}
				/>
			),
			icon: <TeamOutlined />,
		},
	];

	return (
		<>
			<Badge count={unreadCount} size="small" offset={[-2, 2]}>
				<div
					className="cursor-pointer hover:bg-gray-100 p-1 rounded-sm transition-colors"
					onClick={showDrawer}
				>
					<BellFilled className="font-bold text-[20px]" />
				</div>
			</Badge>
			<Drawer
				title="消息通知"
				placement="right"
				onClose={onClose}
				open={visible}
				width={400}
				extra={
					<div className="flex gap-2">
						<Button type="link" size="small" onClick={markAllAsRead}>
							全部已读
						</Button>
						<Button type="link" size="small" danger onClick={clearNotifications}>
							清空
						</Button>
					</div>
				}
			>
				<Tabs
					defaultActiveKey="all"
					activeKey={activeTab}
					onChange={(key) => setActiveTab(key as 'all' | 'team')}
					items={items}
				/>
			</Drawer>
		</>
	);
};

export default NotificationDrawer;
