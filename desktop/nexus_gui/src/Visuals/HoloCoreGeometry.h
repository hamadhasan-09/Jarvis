
#pragma once

#include <QQuick3DGeometry>

class HoloCoreGeometry : public QQuick3DGeometry
{
    Q_OBJECT
    Q_PROPERTY(bool outer READ outer WRITE setOuter NOTIFY outerChanged)

public:
    explicit HoloCoreGeometry();

    bool outer() const { return m_outer; }
    void setOuter(bool value) { if(m_outer==value)return; m_outer=value; rebuildGeometry(); emit outerChanged(); }
signals:
    void outerChanged();
private:
    bool m_outer=false;
    void rebuildGeometry();
};
