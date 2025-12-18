import React from 'react';
import ReactECharts from 'echarts-for-react';
import { Card, Typography, Select } from 'antd';

const { Title } = Typography;

const AnalysisChart: React.FC = () => {
	const option = {
		tooltip: {
			trigger: 'axis',
			axisPointer: {
				type: 'cross',
				label: {
					backgroundColor: '#6a7985',
				},
			},
		},
		legend: {
			data: ['Visits', 'Orders', 'Payments'],
			bottom: 0,
		},
		grid: {
			left: '3%',
			right: '4%',
			bottom: '10%',
			containLabel: true,
		},
		xAxis: [
			{
				type: 'category',
				boundaryGap: false,
				data: ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'],
				axisLine: {
					show: false,
				},
				axisTick: {
					show: false,
				},
			},
		],
		yAxis: [
			{
				type: 'value',
				splitLine: {
					lineStyle: {
						type: 'dashed',
					},
				},
			},
		],
		series: [
			{
				name: 'Visits',
				type: 'line',
				stack: 'Total',
				smooth: true,
				lineStyle: {
					width: 3,
					color: '#3b82f6',
				},
				showSymbol: false,
				areaStyle: {
					opacity: 0.1,
					color: '#3b82f6',
				},
				emphasis: {
					focus: 'series',
				},
				data: [120, 132, 101, 134, 90, 230, 210],
			},
			{
				name: 'Orders',
				type: 'line',
				stack: 'Total',
				smooth: true,
				lineStyle: {
					width: 3,
					color: '#10b981',
				},
				showSymbol: false,
				areaStyle: {
					opacity: 0.1,
					color: '#10b981',
				},
				emphasis: {
					focus: 'series',
				},
				data: [220, 182, 191, 234, 290, 330, 310],
			},
			{
				name: 'Payments',
				type: 'line',
				stack: 'Total',
				smooth: true,
				lineStyle: {
					width: 3,
					color: '#f59e0b',
				},
				showSymbol: false,
				areaStyle: {
					opacity: 0.1,
					color: '#f59e0b',
				},
				emphasis: {
					focus: 'series',
				},
				data: [150, 232, 201, 154, 190, 330, 410],
			},
		],
	};

	return (
		<Card className="mb-6 shadow-sm hover:shadow-md transition-shadow">
			<div className="flex justify-between items-center mb-4">
				<div>
					<Title level={4} style={{ margin: 0 }}>
						Web Analytic
					</Title>
					<span className="text-gray-500">
						Explore the metrics to understand trends and drive.
					</span>
				</div>
				<Select
					defaultValue="day"
					style={{ width: 120 }}
					options={[
						{ value: 'day', label: 'Day' },
						{ value: 'week', label: 'Week' },
						{ value: 'month', label: 'Month' },
					]}
				/>
			</div>

			<div className="flex gap-8 mb-4">
				<div>
					<div className="text-gray-500 text-sm">Page views</div>
					<div className="text-2xl font-bold flex items-center gap-2">
						32,124
						<span className="text-green-500 text-sm font-normal">↑ 4.2%</span>
					</div>
				</div>
				<div>
					<div className="text-gray-500 text-sm">Avg. Time on page</div>
					<div className="text-2xl font-bold flex items-center gap-2">
						3m 16s
						<span className="text-red-500 text-sm font-normal">↓ 0.2%</span>
					</div>
				</div>
			</div>

			<ReactECharts option={option} style={{ height: 350 }} />
		</Card>
	);
};

export default AnalysisChart;
