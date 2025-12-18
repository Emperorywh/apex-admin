import React from 'react';
import { Card, Table, Typography, Progress, Button } from 'antd';
import { DownloadOutlined } from '@ant-design/icons';

const { Title } = Typography;

const TrafficData: React.FC = () => {
	const dataSource = [
		{
			key: '1',
			source: 'Direct',
			visits: '1,500',
			unique: '1,200',
			bounce: '↑ 40%',
			duration: '00:03:45',
			progress: 60,
		},
		{
			key: '2',
			source: 'Natural',
			visits: '3,000',
			unique: '2,500',
			bounce: '↑ 35%',
			duration: '00:04:20',
			progress: 75,
		},
		{
			key: '3',
			source: 'Referral',
			visits: '1,000',
			unique: '850',
			bounce: '↑ 45%',
			duration: '00:03:10',
			progress: 80,
		},
		{
			key: '4',
			source: 'Social Media',
			visits: '2,000',
			unique: '1,800',
			bounce: '↑ 50%',
			duration: '00:02:50',
			progress: 40,
		},
	];

	const columns = [
		{
			title: 'SOURCE',
			dataIndex: 'source',
			key: 'source',
			render: (text: string) => <span className="font-medium text-gray-700">{text}</span>,
		},
		{
			title: 'VISITS',
			dataIndex: 'visits',
			key: 'visits',
		},
		{
			title: 'UNIQUE VISITORS',
			dataIndex: 'unique',
			key: 'unique',
		},
		{
			title: 'BOUNCE RATE',
			dataIndex: 'bounce',
			key: 'bounce',
			render: (text: string) => <span className="text-green-500">{text}</span>,
		},
		{
			title: 'AVG. SESSION DURATION',
			dataIndex: 'duration',
			key: 'duration',
		},
		{
			title: 'PROGRESS TO GOAL (%)',
			dataIndex: 'progress',
			key: 'progress',
			render: (percent: number) => (
				<div className="w-32">
					<Progress
						percent={percent}
						size="small"
						showInfo={true}
						strokeColor="#3b82f6"
					/>
				</div>
			),
		},
	];

	return (
		<Card className="mt-6 shadow-sm hover:shadow-md transition-shadow">
			<div className="flex justify-between items-center mb-4">
				<Title level={5} style={{ margin: 0 }}>
					Traffic data
				</Title>
				<Button icon={<DownloadOutlined />} size="small">
					Export data
				</Button>
			</div>
			<Table
				dataSource={dataSource}
				columns={columns}
				pagination={false}
				size="small"
				scroll={{ x: true }}
			/>
		</Card>
	);
};

export default TrafficData;
