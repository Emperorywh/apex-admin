import React from 'react';
import { Dropdown } from 'antd';
import { useTranslation } from 'react-i18next';
import CnLogo from "@/assets/svg/cn.svg?react";
import GbLogo from "@/assets/svg/gb.svg?react";
import type { MenuProps } from 'antd';

export interface LanguageSelectorProps {
    className?: string;
    placement?: "bottom" | "bottomLeft" | "bottomRight" | "top" | "topLeft" | "topRight";
}

const LanguageSelector: React.FC<LanguageSelectorProps> = ({ 
    className = "flex items-center cursor-pointer hover:bg-gray-100 p-1 rounded-sm transition-colors",
    placement = "bottom"
}) => {
    const { i18n } = useTranslation();

    const items: MenuProps['items'] = [
        {
            label: (
                <div className="flex items-center gap-2">
                    <CnLogo className="w-5 h-5 rounded-sm" />
                    <span>中文</span>
                </div>
            ),
            key: 'zh-CN',
        },
        {
            label: (
                <div className="flex items-center gap-2">
                    <GbLogo className="w-5 h-5 rounded-sm" />
                    <span>English</span>
                </div>
            ),
            key: 'en-US',
        },
    ];

    const handleLanguageChange: MenuProps['onClick'] = ({ key }) => {
        i18n.changeLanguage(key);
    };

    return (
        <Dropdown
            menu={{
                items,
                onClick: handleLanguageChange
            }}
            placement={placement}
            arrow
            trigger={['click']}
        >
            <div className={className}>
                {i18n.language === "zh-CN" ? <CnLogo className="w-5 h-5 rounded-sm" /> : <GbLogo className="w-5 h-5 rounded-sm" />}
            </div>
        </Dropdown>
    );
};

export default LanguageSelector;
