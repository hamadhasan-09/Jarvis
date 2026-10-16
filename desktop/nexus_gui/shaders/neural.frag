VARYING vec3 localPos;
VARYING vec3 localNormal;
VARYING vec2 coord;
void MAIN() {
    float dist=abs(fract(coord.x-time*(0.20+speech*0.14)+coord.y)-0.5);
    float pulse=exp(-dist*dist*1100.0);
    float gain=(0.075+pulse*1.8)*(0.025+assembly)*(1.0+speech*0.5);
    FRAGCOLOR=vec4(vec3(0.10,0.74,1.0)*gain,1.0);
}
