import { useAppNavigate } from '@/hooks/useAppNavigate';
import { Button, Input } from 'antd';
import React, { useEffect } from 'react';

const Page: React.FC = () => {

    const { push } = useAppNavigate();

    useEffect(() => {
        console.log("个人资料挂载");
        return () => {
            console.log("个人资料卸载");
        }
    }, [])

    return (
        <div>
            <Button
                type="primary"
                onClick={() => {
                    push('/user/profile', {
                        query: {
                            tab: 'profile',
                        },
                        title: "新个人资料",
                    });
                }}>
                多开Tab页
            </Button>
            <h1 className='text-[20px] font-bold my-3'>个人资料</h1>
            <Input placeholder='profile' />
        </div>
    );
};

export default Page;
