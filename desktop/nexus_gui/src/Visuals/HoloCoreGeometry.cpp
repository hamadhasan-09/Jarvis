
#include "HoloCoreGeometry.h"

#include <QVector3D>
#include <QByteArray>

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <unordered_map>
#include <vector>

namespace {

using Face = std::array<int, 3>;

int midpoint(
    int a,
    int b,
    std::vector<QVector3D>& points,
    std::unordered_map<std::uint64_t, int>& cache)
{
    const int low = std::min(a, b);
    const int high = std::max(a, b);

    const std::uint64_t key =
        (static_cast<std::uint64_t>(low) << 32)
        | static_cast<std::uint64_t>(high);

    const auto found = cache.find(key);

    if (found != cache.end())
        return found->second;

    const QVector3D middle =
        (points[a] + points[b]).normalized();

    const int index = static_cast<int>(points.size());

    points.push_back(middle);
    cache.emplace(key, index);

    return index;
}

QVector3D direction(float x, float y, float z)
{
    return QVector3D(x, y, z).normalized();
}

float peak(
    const QVector3D& point,
    const QVector3D& axis,
    float sharpness)
{
    const float amount = std::max(
        0.0f,
        QVector3D::dotProduct(point, axis)
    );

    return std::pow(amount, sharpness);
}

// Turn a unit direction into an irregular crystalline surface point.
QVector3D surfacePoint(const QVector3D& d)
{
    const float wave1 = std::sin(
        5.2f * d.x() + 2.8f * d.y() - 1.9f * d.z()
    );

    const float wave2 = std::cos(
        3.4f * d.y() + 6.0f * d.z() + 1.2f * d.x()
    );

    const float wave3 = std::sin(
        8.0f * d.z() - 3.0f * d.x() + 2.0f * d.y()
    );

    float radius =
        86.0f
        + 16.0f * wave1
        + 12.0f * wave2
        + 7.0f * wave3;

    // Uneven crystalline projections.
    radius += 38.0f * peak(
        d, direction(0.82f, 0.25f, 0.50f), 9.0f
    );

    radius += 30.0f * peak(
        d, direction(-0.65f, 0.70f, 0.28f), 10.0f
    );

    radius += 34.0f * peak(
        d, direction(0.10f, -0.35f, -0.93f), 9.0f
    );

    radius += 24.0f * peak(
        d, direction(-0.80f, -0.48f, -0.35f), 11.0f
    );

    // Small asymmetrical indentations.
    radius -= 12.0f * peak(
        d, direction(0.35f, 0.82f, -0.42f), 12.0f
    );

    return d * radius;
}

} // namespace

HoloCoreGeometry::HoloCoreGeometry()
{
    rebuildGeometry();
}

void HoloCoreGeometry::rebuildGeometry()
{
    constexpr float t = 1.61803398875f;

    // Start with an icosahedron instead of a sphere primitive.
    std::vector<QVector3D> points = {
        direction(-1,  t,  0),
        direction( 1,  t,  0),
        direction(-1, -t,  0),
        direction( 1, -t,  0),
        direction( 0, -1,  t),
        direction( 0,  1,  t),
        direction( 0, -1, -t),
        direction( 0,  1, -t),
        direction( t,  0, -1),
        direction( t,  0,  1),
        direction(-t,  0, -1),
        direction(-t,  0,  1)
    };

    std::vector<Face> faces = {
        {0,11,5}, {0,5,1}, {0,1,7}, {0,7,10}, {0,10,11},
        {1,5,9}, {5,11,4}, {11,10,2}, {10,7,6}, {7,1,8},
        {3,9,4}, {3,4,2}, {3,2,6}, {3,6,8}, {3,8,9},
        {4,9,5}, {2,4,11}, {6,2,10}, {8,6,7}, {9,8,1}
    };

    // Subdivide the surface to create enough detail for sharp projections.
    for (int level = 0; level < 3; ++level) {
        std::unordered_map<std::uint64_t, int> cache;
        std::vector<Face> refined;
        refined.reserve(faces.size() * 4);

        for (const Face& f : faces) {
            const int a = midpoint(f[0], f[1], points, cache);
            const int b = midpoint(f[1], f[2], points, cache);
            const int c = midpoint(f[2], f[0], points, cache);

            refined.push_back({f[0], a, c});
            refined.push_back({f[1], b, a});
            refined.push_back({f[2], c, b});
            refined.push_back({a, b, c});
        }

        faces = std::move(refined);
    }

    // Use separate triangle vertices to create a faceted surface.
    clear();
    constexpr int floatsPerVertex = 8;
    QByteArray data;
    data.resize(
        static_cast<qsizetype>(
            faces.size() * 3 * floatsPerVertex * sizeof(float)
        )
    );

    float* output = reinterpret_cast<float*>(data.data());

    int faceIndex=0;
    for (const Face& f : faces) {
        const int fi=faceIndex++;
        const QVector3D dc=(points[f[0]]+points[f[1]]+points[f[2]]).normalized();
        // Coherent missing sectors, not random per-frame noise.
        if(m_outer && (std::sin(dc.x()*5.1f+dc.y()*3.3f+dc.z()*2.8f)>0.48f || fi%19==0)) continue;
        QVector3D p[3] = {
            surfacePoint(points[f[0]]),
            surfacePoint(points[f[1]]),
            surfacePoint(points[f[2]])
        };

        for(auto &v:p) { if(m_outer) v*=1.8f; else v*=0.94f; }
        QVector3D normal = QVector3D::crossProduct(
            p[1] - p[0],
            p[2] - p[0]
        ).normalized();

        const QVector3D center = (p[0] + p[1] + p[2]) / 3.0f;

        // Ensure the front side of each triangle faces outward.
        if (QVector3D::dotProduct(normal, center) < 0.0f) {
            std::swap(p[1], p[2]);
            normal = -normal;
        }

        for (int i = 0; i < 3; ++i) {
            *output++ = p[i].x();
            *output++ = p[i].y();
            *output++ = p[i].z();

            *output++ = normal.x();
            *output++ = normal.y();
            *output++ = normal.z();
            *output++ = i==1 ? 1.0f : 0.0f;
            *output++ = i==2 ? 1.0f : 0.0f;
        }
    }

    setStride(floatsPerVertex * sizeof(float));
    setPrimitiveType(QQuick3DGeometry::PrimitiveType::Triangles);

    addAttribute(
        QQuick3DGeometry::Attribute::PositionSemantic,
        0,
        QQuick3DGeometry::Attribute::F32Type
    );

    addAttribute(
        QQuick3DGeometry::Attribute::NormalSemantic,
        3 * sizeof(float),
        QQuick3DGeometry::Attribute::F32Type
    );

    addAttribute(QQuick3DGeometry::Attribute::TexCoordSemantic,6*sizeof(float),QQuick3DGeometry::Attribute::F32Type);
    data.resize(reinterpret_cast<char*>(output)-data.data());
    setVertexData(data);

    setBounds(
        QVector3D(-300, -300, -300),
        QVector3D(300, 300, 300)
    );
}