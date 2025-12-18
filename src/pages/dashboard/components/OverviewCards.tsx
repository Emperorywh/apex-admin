import React from 'react';
import { Card, Row, Col } from 'antd';
import { UserOutlined, RiseOutlined, FireOutlined } from '@ant-design/icons';

interface StatsCardProps {
	title: string;
	value: string;
	change: string;
	icon: React.ReactNode;
	color: string;
	bg: string;
}

const StatsCard = ({ title, value, change, icon, color, bg }: StatsCardProps) => (
	<Card className="h-full shadow-sm hover:shadow-md transition-shadow">
		<div className="flex justify-between items-start mb-4">
			<span className="text-gray-500 font-medium">{title}</span>
			<div
				className={`w-10 h-10 rounded-full flex items-center justify-center ${bg}`}
				style={{ color: color }}
			>
				{icon}
			</div>
		</div>
		<div className="text-2xl font-bold mb-1">{value}</div>
		<div className={`text-sm ${change.startsWith('↑') ? 'text-green-500' : 'text-red-500'}`}>
			{change} <span className="text-gray-400">vs last day</span>
		</div>
	</Card>
);

const OverviewCards: React.FC = () => {
	const stats = [
		{
			title: 'Visitor',
			value: '149,328',
			change: '↑ 5.2%',
			icon: <UserOutlined style={{ fontSize: '20px' }} />,
			color: '#f97316', // orange
			bg: 'bg-orange-100'
		},
		{
			title: 'Conversion rate',
			value: '6.8%',
			change: '↓ 1.8%',
			icon: <RiseOutlined style={{ fontSize: '20px' }} />,
			color: '#10b981', // green
			bg: 'bg-green-100'
		},
		{
			title: 'Ad campaign clicks',
			value: '17,333',
			change: '↑ 2.3%',
			icon: <FireOutlined style={{ fontSize: '20px' }} />,
			color: '#a855f7', // purple
			bg: 'bg-purple-100'
		}
	];

	return (
		<Row gutter={[16, 16]} className="mb-6">
			{stats.map((stat, index) => (
				<Col xs={24} sm={8} key={index}>
					<StatsCard {...stat} />
				</Col>
			))}
		</Row>
	);
};

export default OverviewCards;
