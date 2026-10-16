#include "NeuralGeometry.h"
#include <QVector3D>
#include <vector>
#include <cmath>
NeuralGeometry::NeuralGeometry() {
    std::vector<float> data;
    auto vertex=[&](QVector3D p,QVector3D n,float u,float seed) {
        data.insert(data.end(),{p.x(),p.y(),p.z(),n.x(),n.y(),n.z(),u,seed});
    };
    // Fixed batch of tube strips. UV.x is distance along each path; UV.y its pulse phase.
    for(int path=0;path<160;++path) {
        const float seed=path*0.61803399f;
        const float az=seed*6.2831853f;
        const float z=-0.93f+1.86f*((path*37%160)/159.0f);
        QVector3D axis(std::sqrt(1-z*z)*std::cos(az),z,std::sqrt(1-z*z)*std::sin(az));
        QVector3D side=QVector3D::crossProduct(axis,QVector3D(0,1,0)).normalized();
        QVector3D up=QVector3D::crossProduct(axis,side).normalized();
        auto point=[&](float t) {
            float r=path<36 ? 12+190*t : 115+85*std::sin(t*2.4f+seed);
            return axis*r+side*(std::sin(t*4.0f+az)*30)+up*(std::cos(t*5+az)*25);
        };
        float radius=path<36 ? 1.15f : 0.32f;
        for(int seg=0;seg<20;++seg) {
            float t0=seg/20.0f,t1=(seg+1)/20.0f;
            QVector3D p0=point(t0),p1=point(t1);
            QVector3D tangent=(p1-p0).normalized();
            QVector3D a=QVector3D::crossProduct(tangent,up).normalized();
            QVector3D b=QVector3D::crossProduct(tangent,a).normalized();
            for(int j=0;j<4;++j) {
                float q=j*1.5707963f,qn=(j+1)*1.5707963f;
                QVector3D n0=a*std::cos(q)+b*std::sin(q), n1=a*std::cos(qn)+b*std::sin(qn);
                vertex(p0+n0*radius,n0,t0,seed);vertex(p1+n0*radius,n0,t1,seed);vertex(p1+n1*radius,n1,t1,seed);
                vertex(p0+n0*radius,n0,t0,seed);vertex(p1+n1*radius,n1,t1,seed);vertex(p0+n1*radius,n1,t0,seed);
            }
        }
    }
    setStride(8*sizeof(float));setPrimitiveType(PrimitiveType::Triangles);
    addAttribute(Attribute::PositionSemantic,0,Attribute::F32Type);
    addAttribute(Attribute::NormalSemantic,3*sizeof(float),Attribute::F32Type);
    addAttribute(Attribute::TexCoordSemantic,6*sizeof(float),Attribute::F32Type);
    setVertexData(QByteArray(reinterpret_cast<const char*>(data.data()),data.size()*sizeof(float)));
    setBounds(QVector3D(-320,-320,-320),QVector3D(320,320,320));
}
