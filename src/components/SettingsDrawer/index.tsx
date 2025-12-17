import React, { useState, useEffect } from 'react';
import { Drawer, Slider, Row, Col, Typography, Button, Tooltip, ColorPicker } from 'antd';
import { SettingFilled, SunOutlined, MoonOutlined, CheckOutlined, FullscreenOutlined, FullscreenExitOutlined, FontSizeOutlined } from '@ant-design/icons';
import { useTranslation } from 'react-i18next';
import { useTheme } from '@/context/ThemeContext';
import type { SettingsDrawerProps, SettingsState, ThemeMode } from './index.types';

const { Text } = Typography;

const PRESET_COLORS = [
	'#1677ff',
	'#ff4d4f',
	'#52c41a',
	'#faad14',
	'#722ed1',
	'#eb2f96',
	'#fa541c',
	'#13c2c2',
];

const SettingsDrawer: React.FC<SettingsDrawerProps> = ({ children, onSettingChange }) => {
	const { t } = useTranslation();
	const { themeMode: globalThemeMode, primaryColor: globalPrimaryColor, setThemeMode, setPrimaryColor } = useTheme();

	const [open, setOpen] = useState(false);
	const [isFullscreen, setIsFullscreen] = useState(false);

	const [settings, setSettings] = useState<SettingsState>({
		themeMode: globalThemeMode,
		layoutMode: 'side',
		primaryColor: globalPrimaryColor,
		fontSize: 14,
	});

	useEffect(() => {
		setSettings((prev) => ({
			...prev,
			themeMode: globalThemeMode,
			primaryColor: globalPrimaryColor,
		}));
	}, [globalThemeMode, globalPrimaryColor]);

	const handleUpdateSettings = <K extends keyof SettingsState>(key: K, value: SettingsState[K]) => {
		const newSettings = { ...settings, [key]: value };
		setSettings(newSettings);
		onSettingChange?.(newSettings);

		if (key === 'themeMode') {
			setThemeMode(value as ThemeMode);
		}
		if (key === 'primaryColor') {
			setPrimaryColor(value as string);
		}
	};

	const toggleFullscreen = () => {
		if (!document.fullscreenElement) {
			document.documentElement.requestFullscreen();
			setIsFullscreen(true);
		} else {
			if (document.exitFullscreen) {
				document.exitFullscreen();
				setIsFullscreen(false);
			}
		}
	};

	useEffect(() => {
		const handleFullscreenChange = () => {
			setIsFullscreen(!!document.fullscreenElement);
		};
		document.addEventListener('fullscreenchange', handleFullscreenChange);
		return () => {
			document.removeEventListener('fullscreenchange', handleFullscreenChange);
		};
	}, []);

	return (
		<>
			<div onClick={() => setOpen(true)} style={{ display: 'inline-block', cursor: 'pointer' }}>
				{children || (
					<div className="group w-[35px] h-[35px] flex items-center justify-center cursor-pointer hover:bg-gray-100 p-1 rounded-full transition-colors">
						<SettingFilled className="font-bold text-[20px] animate-spin-slow group-hover:!text-gray-400 transition-colors" />
					</div>
				)}
			</div>
			<Drawer
				title={t('设置')}
				placement="right"
				onClose={() => setOpen(false)}
				open={open}
				width={320}
				footer={
					<Button block onClick={toggleFullscreen} icon={isFullscreen ? <FullscreenExitOutlined /> : <FullscreenOutlined />}>
						{isFullscreen ? t('退出全屏') : t('全屏')}
					</Button>
				}
			>
				<div className="flex flex-col gap-6">
					{/* Theme Mode */}
					<div>
						<h3 className="mb-3 text-base font-medium text-gray-800 dark:text-gray-200">{t('模式')}</h3>
						<div className="flex gap-4">
							<div
								className={`group flex-1 flex items-center justify-center h-16 rounded-xl border-2 cursor-pointer transition-all duration-300 ${
									settings.themeMode === 'light'
										? 'border-blue-500 bg-blue-50/50 backdrop-blur-sm text-blue-500 shadow-lg shadow-blue-100'
										: 'border-gray-100 hover:border-blue-200 hover:bg-gray-50/50 hover:text-blue-400'
								}`}
								onClick={() => handleUpdateSettings('themeMode', 'light')}
							>
								<SunOutlined className={`text-xl transition-transform duration-300 ${settings.themeMode === 'light' ? 'scale-110' : 'group-hover:scale-110'}`} />
							</div>
							<div
								className={`group flex-1 flex items-center justify-center h-16 rounded-xl border-2 cursor-pointer transition-all duration-300 ${
									settings.themeMode === 'dark'
										? 'border-blue-500 bg-blue-50/50 backdrop-blur-sm text-blue-500 shadow-lg shadow-blue-100'
										: 'border-gray-100 hover:border-blue-200 hover:bg-gray-50/50 hover:text-blue-400'
								}`}
								onClick={() => handleUpdateSettings('themeMode', 'dark')}
							>
								<MoonOutlined className={`text-xl transition-transform duration-300 ${settings.themeMode === 'dark' ? 'scale-110' : 'group-hover:scale-110'}`} />
							</div>
						</div>
					</div>

					{/* Layout */}
					<div>
						<h3 className="mb-3 text-base font-medium text-gray-800 dark:text-gray-200">{t('布局')}</h3>
						<div className="flex gap-4">
							{/* Side Menu */}
							<Tooltip title={t('侧边菜单')}>
								<div
									className={`group relative w-1/3 h-16 rounded-xl border-2 cursor-pointer transition-all duration-300 overflow-hidden ${
										settings.layoutMode === 'side'
											? 'border-blue-500 shadow-lg shadow-blue-100'
											: 'border-gray-100 hover:border-blue-200 hover:shadow-md'
									}`}
									onClick={() => handleUpdateSettings('layoutMode', 'side')}
								>
									<div className="absolute top-0 left-0 w-[20%] h-full bg-gray-800 opacity-20 group-hover:opacity-30 transition-opacity"></div>
									<div className="absolute top-0 right-0 w-[80%] h-[20%] bg-gray-300 opacity-20 group-hover:opacity-30 transition-opacity"></div>
									<div className="absolute bottom-0 right-0 w-[80%] h-[80%] bg-gray-100 opacity-20 group-hover:opacity-30 transition-opacity"></div>
									{settings.layoutMode === 'side' && (
										<div className="absolute bottom-1 right-1 text-blue-500 bg-white rounded-full p-0.5 shadow-sm">
											<CheckOutlined />
										</div>
									)}
								</div>
							</Tooltip>

							{/* Top Menu */}
							<Tooltip title={t('顶部菜单')}>
								<div
									className={`group relative w-1/3 h-16 rounded-xl border-2 cursor-pointer transition-all duration-300 overflow-hidden ${
										settings.layoutMode === 'top'
											? 'border-blue-500 shadow-lg shadow-blue-100'
											: 'border-gray-100 hover:border-blue-200 hover:shadow-md'
									}`}
									onClick={() => handleUpdateSettings('layoutMode', 'top')}
								>
									<div className="absolute top-0 left-0 w-full h-[20%] bg-gray-800 opacity-20 group-hover:opacity-30 transition-opacity"></div>
									<div className="absolute bottom-0 left-0 w-full h-[80%] bg-gray-100 opacity-20 group-hover:opacity-30 transition-opacity"></div>
									{settings.layoutMode === 'top' && (
										<div className="absolute bottom-1 right-1 text-blue-500 bg-white rounded-full p-0.5 shadow-sm">
											<CheckOutlined />
										</div>
									)}
								</div>
							</Tooltip>

							{/* Mix Menu */}
							<Tooltip title={t('混合菜单')}>
								<div
									className={`group relative w-1/3 h-16 rounded-xl border-2 cursor-pointer transition-all duration-300 overflow-hidden ${
										settings.layoutMode === 'mix'
											? 'border-blue-500 shadow-lg shadow-blue-100'
											: 'border-gray-100 hover:border-blue-200 hover:shadow-md'
									}`}
									onClick={() => handleUpdateSettings('layoutMode', 'mix')}
								>
									<div className="absolute top-0 left-0 w-full h-[20%] bg-gray-800 opacity-20 group-hover:opacity-30 transition-opacity"></div>
									<div className="absolute bottom-0 left-0 w-[20%] h-[80%] bg-gray-300 opacity-20 group-hover:opacity-30 transition-opacity"></div>
									<div className="absolute bottom-0 right-0 w-[80%] h-[80%] bg-gray-100 opacity-20 group-hover:opacity-30 transition-opacity"></div>
									{settings.layoutMode === 'mix' && (
										<div className="absolute bottom-1 right-1 text-blue-500 bg-white rounded-full p-0.5 shadow-sm">
											<CheckOutlined />
										</div>
									)}
								</div>
							</Tooltip>
						</div>
					</div>

					{/* Primary Color */}
					<div>
						<h3 className="mb-3 text-base font-medium text-gray-800 dark:text-gray-200">{t('主题色')}</h3>
						<div className="flex flex-col gap-3">
							<div className="flex items-center justify-between">
								<span className="text-gray-500">{t('主色调')}</span>
								<ColorPicker
									value={settings.primaryColor}
									onChange={(_, hex) => handleUpdateSettings('primaryColor', hex)}
									presets={[
										{
											label: t('预设颜色'),
											colors: PRESET_COLORS,
										},
									]}
								/>
							</div>
							<div className="flex flex-wrap gap-2">
								{PRESET_COLORS.map((color) => (
									<div
										key={color}
										className={`w-6 h-6 rounded cursor-pointer transition-all ${settings.primaryColor === color ? 'ring-2 ring-offset-2 ring-blue-500 scale-110' : 'hover:scale-110'}`}
										style={{ backgroundColor: color }}
										onClick={() => handleUpdateSettings('primaryColor', color)}
									/>
								))}
							</div>
						</div>
					</div>

					{/* Font Size */}
					<div>
						<h3 className="mb-3 text-base font-medium text-gray-800 dark:text-gray-200">{t('字体大小')}</h3>
						<div className="bg-gray-50 rounded-lg p-4">
							<Row align="middle" gutter={16}>
								<Col flex="none">
									<FontSizeOutlined style={{ fontSize: 12 }} />
								</Col>
								<Col flex="auto">
									<Slider
										min={12}
										max={20}
										value={settings.fontSize}
										onChange={(value) => handleUpdateSettings('fontSize', value)}
										tooltip={{ formatter: (value) => `${value}px` }}
									/>
								</Col>
								<Col flex="none">
									<FontSizeOutlined style={{ fontSize: 20 }} />
								</Col>
								<Col flex="none">
									<Text code className="ml-2">{settings.fontSize}px</Text>
								</Col>
							</Row>
						</div>
					</div>
				</div>
			</Drawer>
		</>
	);
};

export default SettingsDrawer;
