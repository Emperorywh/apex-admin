import React from 'react';
import { Button, Checkbox, Form, Input, Typography } from 'antd';
import {
    UserOutlined,
    LockOutlined,
    MobileOutlined,
    QrcodeOutlined,
    GithubFilled,
    WechatFilled,
    GoogleCircleFilled
} from '@ant-design/icons';
import { useTranslation } from 'react-i18next';
import ReactLogo from "@/assets/react.svg?react";
import SettingsDrawer from "@/components/SettingsDrawer";
import LanguageSelector from "@/components/LanguageSelector";
import { useAppNavigate } from '@/hooks/useAppNavigate';

const { Title, Text, Link } = Typography;

const Login: React.FC = () => {
    const { t } = useTranslation();
    const { push } = useAppNavigate();

    const onFinish = (values: unknown) => {
        console.log('Received values of form: ', values);
        push('/dashboard');
    };

    return (
        <div className="flex w-full h-screen overflow-hidden">
            {/* Left Side - Login Form */}
            <div className="flex-1 flex flex-col h-full bg-white dark:bg-gray-900 transition-colors duration-300 relative z-10 p-4 sm:p-8 md:p-12 lg:p-24 overflow-y-auto">

                {/* Logo */}
                <div className="flex items-center gap-2 mb-10 sm:mb-20">
                    <ReactLogo className="w-8 h-8 text-blue-500 animate-spin-slow" />
                    <span className="text-xl font-bold text-gray-800 dark:text-white">Apex Admin</span>
                </div>

                {/* Main Content */}
                <div className="w-full max-w-[480px] mx-auto flex-1 flex flex-col justify-center">
                    <div className="mb-8">
                        <Title level={2} className="!mb-2 !text-gray-900 dark:!text-white">
                            {t('登录到您的账户')}
                        </Title>
                        <Text className="text-gray-500 dark:text-gray-400">
                            {t('输入您的账号登录到您的账户')}
                        </Text>
                    </div>

                    <Form
                        name="login"
                        initialValues={{ remember: true }}
                        onFinish={onFinish}
                        layout="vertical"
                        size="large"
                    >
                        <Form.Item
                            name="username"
                            label={<span className="dark:text-gray-300">{t('账号')}</span>}
                            rules={[{ required: true, message: t('请输入您的账号!') }]}
                        >
                            <Input
                                prefix={<UserOutlined className="text-gray-400" />}
                                placeholder="admin"
                                className="dark:bg-gray-800 dark:border-gray-700 dark:text-white"
                            />
                        </Form.Item>

                        <Form.Item
                            name="password"
                            label={<span className="dark:text-gray-300">{t('密码')}</span>}
                            rules={[{ required: true, message: t('请输入您的密码!') }]}
                        >
                            <Input.Password
                                prefix={<LockOutlined className="text-gray-400" />}
                                placeholder="123456"
                                className="dark:bg-gray-800 dark:border-gray-700 dark:text-white"
                            />
                        </Form.Item>

                        <div className="flex justify-between items-center mb-6">
                            <Form.Item name="remember" valuePropName="checked" noStyle>
                                <Checkbox className="dark:text-gray-300">{t('记住我')}</Checkbox>
                            </Form.Item>
                            <a className="text-blue-500 hover:text-blue-600 text-sm font-medium" href="">
                                {t('忘记密码?')}
                            </a>
                        </div>

                        <Form.Item className="mb-4">
                            <Button type="primary" htmlType="submit" block size="large" className="h-10 font-medium">
                                {t('登录')}
                            </Button>
                        </Form.Item>

                        <div className="grid grid-cols-2 gap-4 mb-6">
                            <Button icon={<MobileOutlined />} block className="dark:bg-transparent dark:text-gray-300 dark:border-gray-600">
                                {t('手机登录')}
                            </Button>
                            <Button icon={<QrcodeOutlined />} block className="dark:bg-transparent dark:text-gray-300 dark:border-gray-600">
                                {t('二维码登录')}
                            </Button>
                        </div>
                    </Form>

                    <div className="relative mb-6">
                        <div className="absolute inset-0 flex items-center">
                            <div className="w-full border-t border-gray-200 dark:border-gray-700"></div>
                        </div>
                        <div className="relative flex justify-center text-sm">
                            <span className="px-2 bg-white dark:bg-gray-900 text-gray-500 dark:text-gray-400">
                                {t('其他登录方式')}
                            </span>
                        </div>
                    </div>

                    <div className="flex justify-center gap-6 mb-8">
                        <div className="w-10 h-10 flex items-center justify-center rounded-full border border-gray-200 dark:border-gray-700 cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors">
                            <GithubFilled className="text-xl text-gray-600 dark:text-gray-300" />
                        </div>
                        <div className="w-10 h-10 flex items-center justify-center rounded-full border border-gray-200 dark:border-gray-700 cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors">
                            <WechatFilled className="text-xl text-green-500" />
                        </div>
                        <div className="w-10 h-10 flex items-center justify-center rounded-full border border-gray-200 dark:border-gray-700 cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors">
                            <GoogleCircleFilled className="text-xl text-red-500" />
                        </div>
                    </div>

                    <div className="text-center text-gray-500 dark:text-gray-400">
                        {t('没有账号?')} <Link href="" className="!text-blue-500 hover:!text-blue-600 font-medium">{t('注册')}</Link>
                    </div>
                </div>
            </div>

            {/* Right Side - Image/Illustration */}
            <div className="hidden lg:flex flex-1 relative bg-gray-50 dark:bg-gray-950 items-center justify-center overflow-hidden">
                {/* Top Right Tools */}
                <div className="absolute top-4 right-4 flex items-center gap-4 z-20">
                    <LanguageSelector
                        className="flex items-center cursor-pointer p-2 rounded-full hover:bg-gray-200 dark:hover:bg-gray-800 transition-colors"
                        placement="bottomRight"
                    />
                    <SettingsDrawer />
                </div>

                {/* Abstract Background Shapes */}
                <div className="absolute top-0 left-0 w-full h-full opacity-10 dark:opacity-5 pointer-events-none">
                    <div className="absolute top-[-10%] left-[-10%] w-[50%] h-[50%] bg-blue-500 rounded-full blur-[100px]"></div>
                    <div className="absolute bottom-[-10%] right-[-10%] w-[50%] h-[50%] bg-purple-500 rounded-full blur-[100px]"></div>
                </div>

                {/* Central Illustration Placeholder */}
                <div className="relative z-10 flex flex-col items-center justify-center p-12 text-center">
                    <div className="w-[400px] h-[400px] bg-white dark:bg-gray-800 rounded-full shadow-2xl flex items-center justify-center mb-8 relative overflow-hidden group">
                        <div className="absolute inset-0 bg-gradient-to-tr from-blue-500/10 to-purple-500/10 opacity-0 group-hover:opacity-100 transition-opacity duration-500"></div>
                        <img
                            src="https://gw.alipayobjects.com/zos/rmsportal/KDpgvguMpGfqaHPjicRK.svg"
                            alt="Login Illustration"
                            className="w-[60%] opacity-80 group-hover:scale-110 transition-transform duration-500"
                        />
                        {/* Orbit Circles */}
                        <div className="absolute w-[120%] h-[120%] border border-dashed border-gray-200 dark:border-gray-700 rounded-full animate-spin-slow" style={{ animationDuration: '20s' }}></div>
                        <div className="absolute w-[80%] h-[80%] border border-dashed border-gray-200 dark:border-gray-700 rounded-full animate-reverse-spin" style={{ animationDuration: '15s' }}></div>
                    </div>
                    <h2 className="text-3xl font-bold text-gray-800 dark:text-white mb-4">{t('欢迎使用 Apex Admin')}</h2>
                    <p className="text-gray-500 dark:text-gray-400 max-w-md text-lg">
                        {t('开箱即用的中后台管理系统，提供丰富的组件和模板，帮助您快速搭建现代化应用。')}
                    </p>
                </div>
            </div>
        </div>
    );
};

export default Login;
