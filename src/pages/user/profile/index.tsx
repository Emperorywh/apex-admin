import { Input } from 'antd';
import React, { useEffect } from 'react';

const Page: React.FC = () => {

    useEffect(() => {
        console.log("个人资料挂载");
        return () => {
            console.log("个人资料卸载");
        }
    }, [])

    return (
        <div>
            <h1>个人资料</h1>
            <Input />
        </div>
    );
};

export default Page;
