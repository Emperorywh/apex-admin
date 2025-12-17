import React from 'react';
import { ConfigProvider, theme } from 'antd';
import { useTheme } from '@/context/ThemeContext';
import zhCN from 'antd/locale/zh_CN';
import enUS from 'antd/locale/en_US';
import { useTranslation } from 'react-i18next';
import '@/i18n/i18n';

export const GlobalConfig: React.FC<{ children: React.ReactNode }> = ({ children }) => {
    const { themeMode, primaryColor } = useTheme();
    const { i18n } = useTranslation();

    const antdLocale = i18n.language === 'en-US' ? enUS : zhCN;

    return (
        <ConfigProvider
            locale={antdLocale}
            theme={{
                algorithm: themeMode === 'dark' ? theme.darkAlgorithm : theme.defaultAlgorithm,
                token: {
                    colorPrimary: primaryColor,
                    fontFamily: "'Open Sans', sans-serif",
                },
            }}
        >
            {children}
        </ConfigProvider>
    );
};
