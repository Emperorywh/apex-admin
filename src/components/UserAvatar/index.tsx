import React from 'react';
import { Popover, Divider, Avatar } from 'antd';
import { useTranslation } from 'react-i18next';
import { useAppNavigate } from '@/hooks/useAppNavigate';

const UserAvatar: React.FC = () => {
	const { t } = useTranslation();

	const { push } = useAppNavigate();

	const content = (
		<div className="">
			<Divider size="small" />
			<div className="cursor-pointer hover:bg-gray-100 px-2 py-1 rounded-sm transition-colors">
				<span>{t('个人资料')}</span>
			</div>
			<div className="cursor-pointer hover:bg-gray-100 px-2 py-1 rounded-sm transition-colors">
				<span>{t('账户')}</span>
			</div>
			<Divider size="small" />
			<div
				onClick={() => push('/login')}
				className="cursor-pointer hover:bg-gray-100 px-2 py-1 rounded-sm transition-colors text-[#ff4d4f] hover:text-[#232222]"
			>
				<span>{t('退出')}</span>
			</div>
		</div>
	);

	const title = (
		<div className="flex items-center gap-5">
			<Avatar
				size={'small'}
				src="https://gw.alipayobjects.com/zos/rmsportal/KDpgvguMpGfqaHPjicRK.svg"
			/>
			<div className="flex flex-col text-[14px]">
				<span className="font-bold">admin</span>
				<span className="text-[12px] text-[#666]">imyangwenhua@gmail.com</span>
			</div>
		</div>
	);

	return (
		<Popover content={content} title={title} trigger="click" overlayStyle={{ width: 240 }}>
			<Avatar
				className="cursor-pointer"
				size={'small'}
				src="https://gw.alipayobjects.com/zos/rmsportal/KDpgvguMpGfqaHPjicRK.svg"
			/>
		</Popover>
	);
};

export default UserAvatar;
