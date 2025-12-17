import React from 'react';
import ReactECharts from 'echarts-for-react';
import { Card, Typography } from 'antd';

const { Title } = Typography;

const DeviceChart: React.FC = () => {
	const option = {
		tooltip: {
			trigger: 'item'
		},
		legend: {
			bottom: '0%',
			left: 'center',
			icon: 'circle'
		},
		series: [
			{
				name: 'Access From',
				type: 'pie',
				radius: ['40%', '70%'],
				avoidLabelOverlap: false,
				itemStyle: {
					borderRadius: 10,
					borderColor: '#fff',
					borderWidth: 2
				},
				label: {
					show: false,
					position: 'center'
				},
				emphasis: {
					label: {
						show: true,
						fontSize: 20,
						fontWeight: 'bold'
					}
				},
				labelLine: {
					show: false
				},
				data: [
					{ value: 1048, name: 'Desktop', itemStyle: { color: '#3b82f6' } },
					{ value: 735, name: 'Mobile', itemStyle: { color: '#f59e0b' } },
					{ value: 580, name: 'Tablet', itemStyle: { color: '#8b5cf6' } }
				]
			}
		]
	};

	return (
		<Card className="h-full shadow-sm hover:shadow-md transition-shadow">
			<Title level={5} style={{ marginBottom: 20 }}>Session devices</Title>
			<div className="relative">
				<div className="absolute inset-0 flex items-center justify-center pointer-events-none">
					<div className="text-center">
						<div className="text-gray-500 text-xs">Total</div>
						<div className="font-bold text-xl">95.4</div>
					</div>
				</div>
				<ReactECharts option={option} style={{ height: 300 }} />
			</div>
		</Card>
	);
};

export default DeviceChart;
