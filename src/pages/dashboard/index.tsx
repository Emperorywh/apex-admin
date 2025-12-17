import React from "react";
import { Row, Col } from "antd";
import AnalysisChart from "./components/AnalysisChart";
import OverviewCards from "./components/OverviewCards";
import TopPages from "./components/TopPages";
import DeviceChart from "./components/DeviceChart";
import TrafficData from "./components/TrafficData";

const Page: React.FC = () => {
    return (
        <div className="p-4">
            <div className="mb-6">
                <AnalysisChart />
            </div>
            <div className="mb-6">
                <OverviewCards />
            </div>
            <Row gutter={[16, 16]}>
                <Col className="mb-6" xs={24} lg={16}>
                    <TopPages />
                </Col>
                <Col className="mb-6" xs={24} lg={8}>
                    <DeviceChart />
                </Col>
            </Row>
            <TrafficData />
        </div>
    )
}

export default Page
