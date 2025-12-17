import React from 'react';
import { Card, Table, Typography, Button } from 'antd';
import { DownloadOutlined } from '@ant-design/icons';

const { Title } = Typography;

const TopPages: React.FC = () => {
	const dataSource = [
		{
			key: '1',
			url: '/dashboard',
			views: '6,485',
			viewsChange: '↑ 1.7%',
			unique: '1,078',
			uniqueChange: '↑ 1.2%'
		},
		{
			key: '2',
			url: '/affiliate',
			views: '3,687',
			viewsChange: '↑ 1.4%',
			unique: '801',
			uniqueChange: '↑ 0.9%'
		},
		{
			key: '3',
			url: '/contact',
			views: '2,918',
			viewsChange: '↑ 2.6%',
			unique: '655',
			uniqueChange: '↑ 1.4%'
		},
		{
			key: '4',
			url: '/products',
			views: '4,882',
			viewsChange: '↓ 0.7%',
			unique: '936',
			uniqueChange: '↓ 0.3%'
		},
		{
			key: '5',
			url: '/sign-in',
			views: '1,577',
			viewsChange: '↑ 1.1%',
			unique: '301',
			uniqueChange: '↑ 0.8%'
		},
	];

	const columns = [
		{
			title: 'PAGE URL',
			dataIndex: 'url',
			key: 'url',
			render: (text: string) => <span className="font-medium text-gray-700">{text}</span>
		},
		{
			title: 'VIEWS',
			dataIndex: 'views',
			key: 'views',
			render: (text: string, record: any) => (
				<span>
					{text} <span className={`ml-2 text-xs ${record.viewsChange.includes('↑') ? 'text-green-500' : 'text-red-500'}`}>{record.viewsChange}</span>
				</span>
			)
		},
		{
			title: 'UNIQUE VISITORS',
			dataIndex: 'unique',
			key: 'unique',
			render: (text: string, record: any) => (
				<span>
					{text} <span className={`ml-2 text-xs ${record.uniqueChange.includes('↑') ? 'text-green-500' : 'text-red-500'}`}>{record.uniqueChange}</span>
				</span>
			)
		},
	];

	return (
		<Card className="h-full shadow-sm hover:shadow-md transition-shadow">
			<div className="flex justify-between items-center mb-4">
				<Title level={5} style={{ margin: 0 }}>Top pages</Title>
				<Button icon={<DownloadOutlined />} size="small">Export data</Button>
			</div>
			<Table
				dataSource={dataSource}
				columns={columns}
				pagination={false}
				size="small"
				className="no-border-table"
			/>
		</Card>
	);
};

export default TopPages;
