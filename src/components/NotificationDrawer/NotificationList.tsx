import React from 'react';
import { Avatar, Empty, Typography, Tag, Button, Tooltip } from 'antd';
import { MessageOutlined, CheckCircleOutlined } from '@ant-design/icons';
import dayjs from 'dayjs';
import type { NotificationListProps } from './index.types';

const { Text, Paragraph } = Typography;

const NotificationList: React.FC<NotificationListProps> = ({ data, onMarkAsRead }) => {
	if (data.length === 0) {
		return (
			<div className="flex justify-center items-center py-10">
				<Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无消息" />
			</div>
		);
	}

	return (
		<div className="flex flex-col">
			{data.map((item) => (
				<div
					key={item.id}
					className={`flex gap-4 px-4 py-3 border-b border-gray-100 hover:bg-gray-50 transition-colors cursor-pointer relative group ${!item.read ? 'bg-blue-50/30' : ''
						}`}
				>
					{/* Avatar Section */}
					<div className="shrink-0 pt-1">
						{item.avatar ? (
							<Avatar src={item.avatar} />
						) : (
							<Avatar icon={<MessageOutlined />} className="bg-blue-100 text-blue-600" />
						)}
					</div>

					{/* Content Section */}
					<div className="flex-1 min-w-0">
						<div className="flex justify-between items-start mb-1">
							<Text
								strong={!item.read}
								className={`truncate pr-2 ${item.read ? 'text-gray-500' : 'text-gray-800'}`}
							>
								{item.title}
							</Text>
							{item.group === 'team' && <Tag color="blue" className="shrink-0 ml-auto mr-0">团队</Tag>}
						</div>

						<Paragraph className="mb-1 text-gray-500 text-xs" ellipsis={{ rows: 2 }}>
							{item.description}
						</Paragraph>

						<Text type="secondary" className="text-xs">
							{dayjs(item.datetime).fromNow()}
						</Text>
					</div>

					{/* Actions Section - Only visible on hover or if unread */}
					{!item.read && (
						<div className="absolute right-2 top-1/2 -translate-y-1/2 opacity-0 group-hover:opacity-100 transition-opacity">
							<Tooltip title="标记为已读">
								<Button
									type="text"
									size="small"
									icon={<CheckCircleOutlined className="text-blue-500" />}
									onClick={(e) => {
										e.stopPropagation();
										onMarkAsRead(item.id);
									}}
								/>
							</Tooltip>
						</div>
					)}
				</div>
			))}
		</div>
	);
};

export default NotificationList;
