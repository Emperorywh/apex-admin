import React from 'react';
import { ConfigProvider, theme } from 'antd';
import { useTheme } from '@/context/ThemeContext';
import zhCN from 'antd/locale/zh_CN';
import enUS from 'antd/locale/en_US';
import { useTranslation } from 'react-i18next';
import '@/i18n/i18n';

export const GlobalConfig: React.FC<{ children: React.ReactNode }> = ({ children }) => {
    const { themeMode, primaryColor, fontSize, compactMode, borderRadius } = useTheme();
    const { i18n } = useTranslation();

    const antdLocale = i18n.language === 'en-US' ? enUS : zhCN;

    const getAlgorithm = () => {
        const algorithms = [];
        if (themeMode === 'dark') {
            algorithms.push(theme.darkAlgorithm);
        } else {
            algorithms.push(theme.defaultAlgorithm);
        }

        if (compactMode) {
            algorithms.push(theme.compactAlgorithm);
        }
        return algorithms;
    };

    return (
        <ConfigProvider
            locale={antdLocale}
            theme={{
                algorithm: getAlgorithm(),
                token: {
                    colorPrimary: primaryColor,
                    fontSize: fontSize,
                    borderRadius: borderRadius,
                    fontFamily: "'Open Sans', sans-serif",
                },
            }}
        >
            {children}
        </ConfigProvider>
    );
};
