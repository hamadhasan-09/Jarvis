VARYING vec3 localPos;
VARYING vec3 localNormal;
VARYING vec2 coord;
void MAIN() {
    vec3 d=normalize(VERTEX+vec3(0.001));
    float wave=sin(d.x*5.0+d.y*3.0+time*0.42)*cos(d.z*4.0-time*0.31);
    float fold=sin(d.y*7.0+d.z*3.0+time*0.23);
    VERTEX *= 0.80+assembly*0.20;
    VERTEX += d*(wave*13.0+fold*8.0)*(0.5+assembly*0.5)+d*speech*7.0;
    POSITION = MODELVIEWPROJECTION_MATRIX * vec4(VERTEX, 1.0);
    localPos=VERTEX;localNormal=NORMAL;coord=UV0;
}
